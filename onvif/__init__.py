"""ONVIF Adapter for Yingshi C6C (Ezviz)"""

import logging
import subprocess
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from http.server import HTTPServer, BaseHTTPRequestHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("onvif")

HOST_IP = "192.168.1.138"
ONVIF_PORT = 8089
MJPEG_PORT = 8555
DEVICE_NAME = "YSC6C-C6C"
MANUFACTURER = "Hikvision"
MODEL = "CS-CV246"
DEVICE_UUID = str(uuid.uuid4())
SCOPES = [
    "onvif://www.onvif.org/type/video_encoder",
    "onvif://www.onvif.org/type/ptz",
    "onvif://www.onvif.org/Profile/Streaming",
]
UUID_URN = f"urn:uuid:{DEVICE_UUID}"
PROFILE_TOKEN = "main"
VIDEO_SRC_TOKEN = "vs"
VIDEO_ENC_TOKEN = "ve"
PTZ_NODE_TOKEN = "ptz"
PTZ_CONFIG_TOKEN = "ptzcfg"

STREAM_URL: str | None = None
_snapshot_cache = b""
_snapshot_lock = threading.Lock()
PTZ_CONTROLLER = None
_CLOUD_CAPTURE_FN = None
_SNAPSHOT_LIVE_PATH = "/tmp/snapshot_live.jpg"
_snapshot_ffmpeg_proc: subprocess.Popen | None = None


def soap_response(body: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope"'
        ' xmlns:tds="http://www.onvif.org/ver10/device/wsdl"'
        ' xmlns:trt="http://www.onvif.org/ver10/media/wsdl"'
        ' xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"'
        ' xmlns:tt="http://www.onvif.org/ver10/schema">'
        f'<SOAP-ENV:Body>{body}</SOAP-ENV:Body>'
        '</SOAP-ENV:Envelope>'
    ).encode()


class ONVIFHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", errors="ignore")
        action = self.headers.get("SOAPAction", "").strip('"')
        op = action.split("/")[-1] if action else ""
        print(f"[ONVIF] {op}")
        resp = self._handle(op, body)
        self.send_response(200)
        self.send_header("Content-Type", "application/soap+xml; charset=utf-8")
        self.send_header("Content-Length", len(resp))
        self.end_headers()
        self.wfile.write(resp)

    def do_GET(self):
        if self.path == "/snapshot.jpg":
            data = None
            try:
                with open(_SNAPSHOT_LIVE_PATH, "rb") as f:
                    live = f.read()
                if live[:2] == b"\xff\xd8" and len(live) > 1000:
                    data = live
            except (FileNotFoundError, OSError):
                pass
            if not data:
                with _snapshot_lock:
                    data = _snapshot_cache
            if data:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", len(data))
                self.end_headers()
                self.wfile.write(data)
                return
        self.send_response(200)
        self.send_header("Content-Type", "text/xml")
        self.end_headers()
        self.wfile.write(b"<Envelope/>")

    def _handle(self, op: str, body: str) -> bytes:
        h = {
            "GetServices": lambda: soap_response(
                f"<tds:GetServicesResponse>"
                f'<tds:Service><tds:Namespace>http://www.onvif.org/ver10/device/wsdl</tds:Namespace>'
                f'<tds:XAddr>http://{HOST_IP}:{ONVIF_PORT}/onvif/device_service</tds:XAddr>'
                f'<tds:Version><tt:Major>2</tt:Major><tt:Minor>4</tt:Minor></tds:Version>'
                f"</tds:Service>"
                f'<tds:Service><tds:Namespace>http://www.onvif.org/ver10/media/wsdl</tds:Namespace>'
                f'<tds:XAddr>http://{HOST_IP}:{ONVIF_PORT}/onvif/device_service</tds:XAddr>'
                f'<tds:Version><tt:Major>2</tt:Major><tt:Minor>4</tt:Minor></tds:Version>'
                f"</tds:Service>"
                f'<tds:Service><tds:Namespace>http://www.onvif.org/ver20/ptz/wsdl</tds:Namespace>'
                f'<tds:XAddr>http://{HOST_IP}:{ONVIF_PORT}/onvif/device_service</tds:XAddr>'
                f'<tds:Version><tt:Major>2</tt:Major><tt:Minor>4</tt:Minor></tds:Version>'
                f"</tds:Service></tds:GetServicesResponse>"
            ),
            "GetServiceCapabilities": lambda: soap_response("<tds:GetServiceCapabilitiesResponse/>"),
            "GetCapabilities": lambda: soap_response("<tds:GetCapabilitiesResponse/>"),
            "GetDeviceInformation": lambda: soap_response(
                f"<tds:GetDeviceInformationResponse>"
                f"<tds:Manufacturer>{MANUFACTURER}</tds:Manufacturer>"
                f"<tds:Model>{MODEL}</tds:Model>"
                f"<tds:FirmwareVersion>1.0</tds:FirmwareVersion>"
                f"<tds:SerialNumber>{DEVICE_UUID[:8]}</tds:SerialNumber>"
                f"<tds:HardwareId>v1</tds:HardwareId>"
                f"</tds:GetDeviceInformationResponse>"
            ),
            "GetSystemDateAndTime": lambda: soap_response("<tds:GetSystemDateAndTimeResponse/>"),
            "GetNetworkInterfaces": lambda: soap_response("<tds:GetNetworkInterfacesResponse/>"),
            "GetScopes": lambda: soap_response(
                "<tds:GetScopesResponse>"
                + "".join(f"<tds:Scopes>{s}</tds:Scopes>" for s in SCOPES)
                + "</tds:GetScopesResponse>"
            ),

            "GetProfiles": lambda: soap_response(
                f'<trt:GetProfilesResponse><trt:Profiles token="{PROFILE_TOKEN}" fixed="true">'
                f"<tt:Name>Main</tt:Name>"
                f'<tt:VideoSourceConfiguration token="{VIDEO_SRC_TOKEN}"><tt:Name>VS</tt:Name>'
                f'<tt:SourceToken>{VIDEO_SRC_TOKEN}</tt:SourceToken>'
                f'<tt:Bounds x="0" y="0" width="1920" height="1080"/></tt:VideoSourceConfiguration>'
                f'<tt:VideoEncoderConfiguration token="{VIDEO_ENC_TOKEN}"><tt:Name>H264</tt:Name>'
                f'<tt:Encoding>H264</tt:Encoding>'
                f'<tt:Resolution><tt:Width>1920</tt:Width><tt:Height>1080</tt:Height></tt:Resolution>'
                f'<tt:Quality>10</tt:Quality></tt:VideoEncoderConfiguration>'
                f'<tt:PTZConfiguration token="{PTZ_CONFIG_TOKEN}"><tt:Name>PTZ</tt:Name>'
                f'<tt:NodeToken>{PTZ_NODE_TOKEN}</tt:NodeToken></tt:PTZConfiguration>'
                f"</trt:Profiles></trt:GetProfilesResponse>"
            ),
            "GetVideoSources": lambda: soap_response(
                f'<trt:GetVideoSourcesResponse><trt:VideoSources token="{VIDEO_SRC_TOKEN}">'
                f'<tt:Framerate>30</tt:Framerate>'
                f'<tt:Resolution><tt:Width>1920</tt:Width><tt:Height>1080</tt:Height></tt:Resolution>'
                f"</trt:VideoSources></trt:GetVideoSourcesResponse>"
            ),
            "GetStreamUri": lambda: soap_response(
                f'<trt:GetStreamUriResponse><trt:MediaUri>'
                f'<tt:Uri>http://{HOST_IP}:{MJPEG_PORT}/stream</tt:Uri>'
                f'<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>'
                f'<tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>'
                f'<tt:Timeout>PT0S</tt:Timeout></trt:MediaUri></trt:GetStreamUriResponse>'
            ),
            "GetSnapshotUri": lambda: soap_response(
                f'<trt:GetSnapshotUriResponse><trt:MediaUri>'
                f'<tt:Uri>http://{HOST_IP}:{ONVIF_PORT}/snapshot.jpg</tt:Uri>'
                f'</trt:MediaUri></trt:GetSnapshotUriResponse>'
            ),
            "GetVideoEncoderConfiguration": lambda: soap_response(
                f'<trt:GetVideoEncoderConfigurationResponse>'
                f'<trt:Configuration token="{VIDEO_ENC_TOKEN}">'
                f"<tt:Name>H264</tt:Name>"
                f"<tt:Encoding>H264</tt:Encoding>"
                f"<tt:Resolution><tt:Width>1920</tt:Width><tt:Height>1080</tt:Height></tt:Resolution>"
                f"<tt:Quality>10</tt:Quality>"
                f"<tt:RateControl><tt:FrameRateLimit>30</tt:FrameRateLimit><tt:EncodingInterval>1</tt:EncodingInterval>"
                f"<tt:BitrateLimit>4096</tt:BitrateLimit></tt:RateControl>"
                f"<tt:H264><tt:GovLength>30</tt:GovLength><tt:H264Profile>High</tt:H264Profile></tt:H264>"
                f"<tt:Multicast><tt:Address><tt:Type>IPv4</tt:Type></tt:Address></tt:Multicast>"
                f"<tt:SessionTimeout>PT5S</tt:SessionTimeout>"
                f"</trt:Configuration></trt:GetVideoEncoderConfigurationResponse>"
            ),
            "GetVideoEncoderConfigurations": lambda: soap_response(
                f'<trt:GetVideoEncoderConfigurationsResponse>'
                f'<trt:Configurations token="{VIDEO_ENC_TOKEN}">'
                f"<tt:Name>H264</tt:Name>"
                f"<tt:Encoding>H264</tt:Encoding>"
                f"<tt:Resolution><tt:Width>1920</tt:Width><tt:Height>1080</tt:Height></tt:Resolution>"
                f"<tt:Quality>10</tt:Quality>"
                f"<tt:RateControl><tt:FrameRateLimit>30</tt:FrameRateLimit><tt:EncodingInterval>1</tt:EncodingInterval>"
                f"<tt:BitrateLimit>4096</tt:BitrateLimit></tt:RateControl>"
                f"<tt:H264><tt:GovLength>30</tt:GovLength><tt:H264Profile>High</tt:H264Profile></tt:H264>"
                f"</trt:Configurations></trt:GetVideoEncoderConfigurationsResponse>"
            ),
            "GetVideoEncoderConfigurationOptions": lambda: soap_response(
                f'<trt:GetVideoEncoderConfigurationOptionsResponse>'
                f'<trt:Options>'
                f'<tt:QualityRange><tt:Min>1</tt:Min><tt:Max>10</tt:Max></tt:QualityRange>'
                f'<tt:ResolutionsAvailable><tt:Width>1920</tt:Width><tt:Height>1080</tt:Height></tt:ResolutionsAvailable>'
                f'<tt:ResolutionsAvailable><tt:Width>1280</tt:Width><tt:Height>720</tt:Height></tt:ResolutionsAvailable>'
                f'<tt:H264Options>'
                f'<tt:ResolutionsAvailable><tt:Width>1920</tt:Width><tt:Height>1080</tt:Height></tt:ResolutionsAvailable>'
                f'<tt:ResolutionsAvailable><tt:Width>1280</tt:Width><tt:Height>720</tt:Height></tt:ResolutionsAvailable>'
                f'<tt:GovLengthRange><tt:Min>1</tt:Min><tt:Max>120</tt:Max></tt:GovLengthRange>'
                f'<tt:FrameRateRange><tt:Min>1</tt:Min><tt:Max>30</tt:Max></tt:FrameRateRange>'
                f'<tt:EncodingProfiles>High</tt:EncodingProfiles>'
                f'<tt:EncodingProfiles>Main</tt:EncodingProfiles>'
                f'<tt:EncodingProfiles>Baseline</tt:EncodingProfiles>'
                f'</tt:H264Options>'
                f'</trt:Options></trt:GetVideoEncoderConfigurationOptionsResponse>'
            ),

            "GetNodes": lambda: soap_response(
                f'<tptz:GetNodesResponse><tptz:PTZNode token="{PTZ_NODE_TOKEN}" FixedHomePosition="false">'
                f"<tt:Name>PTZ</tt:Name>"
                f"<tt:SupportedPTZSpaces>"
                f"<tt:ContinuousPanTiltVelocitySpace>"
                f"<tt:XRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:XRange>"
                f"<tt:YRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:YRange>"
                f"</tt:ContinuousPanTiltVelocitySpace>"
                f"<tt:ContinuousZoomVelocitySpace>"
                f"<tt:XRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:XRange>"
                f"</tt:ContinuousZoomVelocitySpace>"
                f"</tt:SupportedPTZSpaces>"
                f"<tt:MaximumNumberOfPresets>10</tt:MaximumNumberOfPresets>"
                f"<tt:HomeSupported>true</tt:HomeSupported>"
                f"</tptz:PTZNode></tptz:GetNodesResponse>"
            ),
            "GetConfigurations": lambda: soap_response(
                f'<tptz:GetConfigurationsResponse><tptz:PTZConfiguration token="{PTZ_CONFIG_TOKEN}">'
                f"<tt:Name>Default</tt:Name><tt:NodeToken>{PTZ_NODE_TOKEN}</tt:NodeToken>"
                f"<tt:DefaultContinuousPanTiltVelocitySpace>"
                f"http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace"
                f"</tt:DefaultContinuousPanTiltVelocitySpace>"
                f'<tt:DefaultPTZSpeed><tt:PanTilt x="0.5" y="0.5"/><tt:Zoom x="0.5"/></tt:DefaultPTZSpeed>'
                f"<tt:DefaultPTZTimeout>PT5S</tt:DefaultPTZTimeout>"
                f"</tptz:PTZConfiguration></tptz:GetConfigurationsResponse>"
            ),
            "GetConfigurationOptions": lambda: soap_response("<tptz:GetConfigurationOptionsResponse/>"),
            "GetPresets": lambda: soap_response("<tptz:GetPresetsResponse/>"),
            "GetStatus": lambda: soap_response(
                "<tptz:GetStatusResponse><tptz:PTZStatus>"
                '<tt:Position><tt:PanTilt x="0" y="0"/><tt:Zoom x="0"/></tt:Position>'
                "</tptz:PTZStatus></tptz:GetStatusResponse>"
            ),

            "ContinuousMove": lambda: self._continuous_move(body),
            "Stop": lambda: self._ptz_stop(),
        }

        handler = h.get(op, lambda: soap_response("<Response/>"))
        return handler()

    def _continuous_move(self, body: str) -> bytes:
        if PTZ_CONTROLLER is None:
            print("[ONVIF PTZ] PTZ_CONTROLLER=None")
            return soap_response("<tptz:ContinuousMoveResponse/>")
        try:
            root = ET.fromstring(body)
            ns = {
                "tt": "http://www.onvif.org/ver10/schema",
                "tptz": "http://www.onvif.org/ver20/ptz/wsdl",
            }
            vel = root.find(".//tptz:Velocity", ns)
            if vel is not None:
                pt_elem = vel.find("tt:PanTilt", ns)
                zoom_elem = vel.find("tt:Zoom", ns)
                pan = float(pt_elem.get("x", "0")) if pt_elem is not None else 0
                tilt = float(pt_elem.get("y", "0")) if pt_elem is not None else 0
                zoom = float(zoom_elem.get("x", "0")) if zoom_elem is not None else 0
                print(f"[ONVIF PTZ] pan={pan}, tilt={tilt}, zoom={zoom}")

                isapi_pan = int(pan * 100)
                isapi_tilt = int(tilt * 100)
                isapi_zoom = int(zoom * 100)

                if abs(isapi_zoom) > 10:
                    threading.Thread(
                        target=lambda: PTZ_CONTROLLER.zoom_in(800) if isapi_zoom > 0 else PTZ_CONTROLLER.zoom_out(800),
                        daemon=True,
                    ).start()
                elif abs(isapi_pan) > 10 or abs(isapi_tilt) > 10:
                    if abs(isapi_pan) > abs(isapi_tilt):
                        direction = "right" if isapi_pan > 0 else "left"
                    else:
                        direction = "up" if isapi_tilt > 0 else "down"
                    print(f"[ONVIF PTZ] calling move({direction})...")
                    threading.Thread(
                        target=lambda: PTZ_CONTROLLER.move(direction, 800),
                        daemon=True,
                    ).start()
                else:
                    print(f"[ONVIF PTZ] velocity too low: pan={isapi_pan}, tilt={isapi_tilt}")
            else:
                print("[ONVIF PTZ] no Velocity element found")
        except Exception as e:
            print(f"[ONVIF PTZ] error: {e}")
            import traceback; traceback.print_exc()
        return soap_response("<tptz:ContinuousMoveResponse/>")

    def _ptz_stop(self) -> bytes:
        if PTZ_CONTROLLER:
            threading.Thread(target=PTZ_CONTROLLER.stop, daemon=True).start()
        return soap_response("<tptz:StopResponse/>")


def start_onvif_server(host: str = "0.0.0.0", port: int = 8089) -> int:
    for offset in range(20):
        try_port = port + offset
        try:
            server = HTTPServer((host, try_port), ONVIFHandler)
            global ONVIF_PORT
            ONVIF_PORT = try_port
            log.info(f"ONVIF http://{HOST_IP}:{try_port}/onvif/device_service")
            server.serve_forever()
            return try_port
        except OSError:
            if offset == 19:
                log.error(f"ONVIF 端口 {port}-{port + 19} 全部被占用")
                raise
            log.debug(f"端口 {try_port} 被占用，尝试 {try_port + 1}")
    return port


def _stop_snapshot_ffmpeg():
    global _snapshot_ffmpeg_proc
    if _snapshot_ffmpeg_proc:
        _snapshot_ffmpeg_proc.terminate()
        try:
            _snapshot_ffmpeg_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _snapshot_ffmpeg_proc.kill()
        _snapshot_ffmpeg_proc = None


def _start_snapshot_ffmpeg(source_url: str) -> subprocess.Popen | None:
    _stop_snapshot_ffmpeg()
    args = [
        "ffmpeg", "-y",
        "-fflags", "nobuffer",
        "-analyzeduration", "100000",
        "-probesize", "50000",
        "-loglevel", "error",
        "-nostdin",
    ]
    if source_url.startswith("rtsp://"):
        args += ["-rtsp_transport", "tcp"]
    args += [
        "-i", source_url,
        "-vf", "fps=5",
        "-f", "image2",
        "-update", "1",
        _SNAPSHOT_LIVE_PATH,
    ]
    try:
        global _snapshot_ffmpeg_proc
        _snapshot_ffmpeg_proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        log.info(f"Snapshot ffmpeg started → {_SNAPSHOT_LIVE_PATH}")
        return _snapshot_ffmpeg_proc
    except FileNotFoundError:
        log.error("ffmpeg not found for snapshot")
        return None


def start_mjpeg_relay_inline(source_url: str, mjpeg_port: int = 8555) -> subprocess.Popen | None:
    args = ["ffmpeg", "-re"]
    if source_url.startswith("rtsp://"):
        args += ["-rtsp_transport", "tcp"]
    args += ["-i", source_url, "-c:v", "copy", "-an",
             "-f", "mpegts", f"http://0.0.0.0:{mjpeg_port}/stream"]
    try:
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log.info(f"H.264 passthrough http://{HOST_IP}:{mjpeg_port}/stream")
        threading.Thread(target=_snapshot_refresh_loop, daemon=True).start()
        return proc
    except FileNotFoundError:
        log.error("ffmpeg not found")
        return None


def _snapshot_refresh_loop():
    global _snapshot_cache, _snapshot_ffmpeg_proc
    log.info("Snapshot cache refresh started")
    last_capture = 0
    fail_count = 0
    last_url = None
    ffmpeg_started_at = 0

    while True:
        source = STREAM_URL
        if source:
            if (source != last_url
                    or _snapshot_ffmpeg_proc is None
                    or _snapshot_ffmpeg_proc.poll() is not None):
                last_url = source
                proc = _start_snapshot_ffmpeg(source)
                if proc is not None:
                    ffmpeg_started_at = time.time()
                    time.sleep(0.5)
                    continue
                fail_count += 1

            if time.time() - ffmpeg_started_at > 0.5:
                try:
                    with open(_SNAPSHOT_LIVE_PATH, "rb") as f:
                        data = f.read()
                    if data[:2] == b"\xff\xd8" and len(data) > 1000:
                        with _snapshot_lock:
                            _snapshot_cache = data
                        fail_count = 0
                        time.sleep(0.1)
                        continue
                except FileNotFoundError:
                    pass
                except Exception:
                    pass

            if _snapshot_ffmpeg_proc and _snapshot_ffmpeg_proc.poll() is not None:
                log.warning("Snapshot ffmpeg exited, restarting...")
                _snapshot_ffmpeg_proc = None
                fail_count += 1
        else:
            fail_count += 1

        now = time.time()
        if now - last_capture > 5 and _CLOUD_CAPTURE_FN:
            try:
                data = _CLOUD_CAPTURE_FN()
                if data and len(data) > 1000:
                    with _snapshot_lock:
                        _snapshot_cache = data
                    last_capture = now
                    fail_count = 0
            except Exception:
                pass
        time.sleep(0.5)
