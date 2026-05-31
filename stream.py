"""直播流模块 — RTSP + 萤石云 FLV"""

import os
import subprocess
from auth import YS7Auth, CameraInfo


def get_rtsp_url(ip: str, username: str, password: str) -> str:
    return f"rtsp://{username}:{password}@{ip}:554/h264/ch1/main/av_stream"


def get_live_address(
    auth: YS7Auth,
    cam: CameraInfo,
    protocol: int = 4,
    quality: int = 1,
    expire_seconds: int = 7200,
) -> str | None:
    import requests
    r = requests.post(
        "https://open.ys7.com/api/lapp/v2/live/address/get",
        data={
            "accessToken": auth.get_access_token(),
            "deviceSerial": cam.serial,
            "channelNo": str(cam.channel_no),
            "protocol": str(protocol),
            "quality": str(quality),
            "expireTime": str(expire_seconds),
        },
    )
    data = r.json()
    if data.get("code") != "200":
        return None
    return data["data"].get("url")


def get_cloud_live_url(auth: YS7Auth, cam: CameraInfo) -> dict | None:
    import requests
    r = requests.post(
        "https://open.ys7.com/api/lapp/live/video/list",
        data={
            "accessToken": auth.get_access_token(),
            "deviceSerial": cam.serial,
            "channelNo": str(cam.channel_no),
        },
    )
    data = r.json()
    if data.get("code") != "200":
        return None
    return data["data"]


def _first_source(data) -> dict | None:
    if isinstance(data, list):
        return data[0] if data else None
    if isinstance(data, dict) and "deviceSerial" in data:
        return data
    return None


def get_cloud_flv_url(auth: YS7Auth, cam: CameraInfo, quality: int = 1) -> str | None:
    url = get_live_address(auth, cam, protocol=4, quality=quality, expire_seconds=604800)
    if url:
        return url
    live = get_cloud_live_url(auth, cam)
    src = _first_source(live) if live else None
    if not src:
        return None
    return (src.get("flvAddress") or src.get("hdFlvAddress")
            if quality == 1 else src.get("flvAddress"))


def get_cloud_hls_url(auth: YS7Auth, cam: CameraInfo, quality: int = 1) -> str | None:
    url = get_live_address(auth, cam, protocol=2, quality=quality, expire_seconds=604800)
    if url:
        return url
    live = get_cloud_live_url(auth, cam)
    src = _first_source(live) if live else None
    if not src:
        return None
    return (src.get("liveAddress") or src.get("hdAddress")
            if quality == 1 else src.get("liveAddress"))


def start_mjpeg_relay(source_url: str, mjpeg_port: int = 8555) -> subprocess.Popen | None:
    args = ["ffmpeg", "-re"]
    if source_url.startswith("rtsp://"):
        args += ["-rtsp_transport", "tcp"]
    args += ["-i", source_url, "-c:v", "mjpeg", "-q:v", "5",
             "-f", "mpjpeg", f"http://0.0.0.0:{mjpeg_port}/stream"]
    try:
        return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        return None


def stop_mjpeg_relay(proc: subprocess.Popen | None):
    if proc:
        proc.terminate()
        proc.wait()


def take_snapshot(source_url: str, output_path: str = "/tmp/c6c_snap.jpg") -> str | None:
    args = ["ffmpeg", "-y"]
    if source_url.startswith("rtsp://"):
        args += ["-rtsp_transport", "tcp"]
    args += ["-i", source_url, "-vframes", "1", "-f", "image2", output_path]
    try:
        subprocess.run(args, capture_output=True, timeout=10)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    return output_path if os.path.exists(output_path) else None


def get_cloud_capture_url(auth: YS7Auth, cam: CameraInfo) -> str | None:
    import requests
    r = requests.post(
        "https://open.ys7.com/api/lapp/device/capture",
        data={
            "accessToken": auth.get_access_token(),
            "deviceSerial": cam.serial,
            "channelNo": str(cam.channel_no),
        },
    )
    data = r.json()
    if data.get("code") != "200":
        return None
    return data["data"].get("picUrl")


def download_capture(auth: YS7Auth, cam: CameraInfo, output_path: str = "/tmp/c6c_capture.jpg") -> str | None:
    import requests
    url = get_cloud_capture_url(auth, cam)
    if not url:
        return None
    r = requests.get(url, timeout=10)
    if r.status_code != 200:
        return None
    with open(output_path, "wb") as f:
        f.write(r.content)
    return output_path
