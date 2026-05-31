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
SNAPSHOT_URL: str | None = None
_snapshot_cache = b""
_snapshot_lock = threading.Lock()
PTZ_CONTROLLER = None


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
        log.debug(op)
        resp = self._handle(op, body)
        self.send_response(200)
        self.send_header("Content-Type", "application/soap+xml; charset=utf-8")
        self.send_header("Content-Length", len(resp))
        self.end_headers()
        self.wfile.write(resp)

    def do_GET(self):
        if self.path == "/snapshot.jpg" and _snapshot_cache:
            with _snapshot_lock:
                data = _snapshot_cache
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
                f'<tt:Uri>rtsp://{HOST_IP}:{MJPEG_PORT}/yc6c_c6c</tt:Uri>'
                f'<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>'
                f'<tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>'
                f'<tt:Timeout>PT0S</tt:Timeout></trt:MediaUri></trt:GetStreamUriResponse>'
            ),
            "GetSnapshotUri": lambda: soap_response(
                f'<trt:GetSnapshotUriResponse><trt:MediaUri>'
                f'<tt:Uri>http://{HOST_IP}:{ONVIF_PORT}/snapshot.jpg</tt:Uri>'
                f'</trt:MediaUri></trt:GetSnapshotUriResponse>'
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
            return soap_response("<tptz:ContinuousMoveResponse/>")
        try:
            root = ET.fromstring(body)
            ns = {"tt": "http://www.onvif.org/ver10/schema"}
            vel = root.find(".//tt:Velocity", ns)
            if vel is not None:
                pt_elem = vel.find("tt:PanTilt", ns)
                zoom_elem = vel.find("tt:Zoom", ns)
                pan = float(pt_elem.get("x", "0")) if pt_elem is not None else 0
                tilt = float(pt_elem.get("y", "0")) if pt_elem is not None else 0
                zoom = float(zoom_elem.get("x", "0")) if zoom_elem is not None else 0

                # ONVIF [-1,1] → ISAPI [-100,100] → duration ~800ms
                isapi_pan = int(pan * 100)
                isapi_tilt = int(tilt * 100)
                isapi_zoom = int(zoom * 100)

                if abs(isapi_zoom) > 10:
                    threading.Thread(
                        target=lambda: PTZ_CONTROLLER.zoom_in(800) if isapi_zoom > 0 else PTZ_CONTROLLER.zoom_out(800),
                        daemon=True,
                    ).start()
                elif abs(isapi_pan) > 10 or abs(isapi_tilt) > 10:
                    # 仅支持单一方向（ISAPI Momentary 不支持同时 pan+tilt）
                    if abs(isapi_pan) > abs(isapi_tilt):
                        direction = "right" if isapi_pan > 0 else "left"
                    else:
                        direction = "up" if isapi_tilt > 0 else "down"
                    threading.Thread(
                        target=lambda: PTZ_CONTROLLER.move(direction, 800),
                        daemon=True,
                    ).start()
        except Exception as e:
            log.error(f"PTZ err: {e}")
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


def start_mjpeg_relay_inline(source_url: str, mjpeg_port: int = 8555) -> subprocess.Popen | None:
    args = ["ffmpeg", "-re"]
    if source_url.startswith("rtsp://"):
        args += ["-rtsp_transport", "tcp"]
    args += ["-i", source_url, "-c:v", "mjpeg", "-q:v", "5",
             "-f", "mpjpeg", f"http://0.0.0.0:{mjpeg_port}/stream"]
    try:
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log.info(f"MJPEG http://{HOST_IP}:{mjpeg_port}/stream")
        threading.Thread(target=_snapshot_refresh_loop, daemon=True).start()
        return proc
    except FileNotFoundError:
        log.error("ffmpeg not found")
        return None


def _snapshot_refresh_loop():
    global _snapshot_cache
    log.info("Snapshot cache refresh started")
    while True:
        source = STREAM_URL
        if not source:
            time.sleep(5)
            continue
        try:
            args = ["ffmpeg", "-y"]
            if source.startswith("rtsp://"):
                args += ["-rtsp_transport", "tcp"]
            args += ["-i", source, "-vframes", "1", "-f", "image2", "pipe:1"]
            result = subprocess.run(args, capture_output=True, timeout=15)
            if result.returncode == 0 and len(result.stdout) > 1000:
                with _snapshot_lock:
                    _snapshot_cache = result.stdout
        except Exception:
            pass
        time.sleep(5)
