"""萤石 C6C ONVIF Adapter — 萤石云 API 后端 + 交互模式"""

import cmd
import argparse
import os
import socket
import sys
import subprocess
import threading

from auth import YS7Auth, CameraInfo
from ptz import PTZController
from stream import get_rtsp_url, get_cloud_flv_url, get_cloud_hls_url, take_snapshot, stop_mjpeg_relay, download_capture
import onvif
from onvif.discovery import run as discovery_run


def detect_host_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def _pick_port(base: int) -> int:
    from http.server import HTTPServer
    for offset in range(20):
        p = base + offset
        try:
            s = HTTPServer(("0.0.0.0", p), onvif.ONVIFHandler)
            s.server_close()
            return p
        except OSError:
            continue
    raise OSError(f"端口 {base}-{base + 19} 全部被占用")


class CameraConsole(cmd.Cmd):
    intro = """
  ╔══════════════════════════════════════════╗
  ║   萤石 C6C 云台控制 — 交互模式          ║
  ║   基于萤石开放平台 API                  ║
  ╚══════════════════════════════════════════╝
  输入 help 查看命令
  """
    prompt = "📷> "

    def __init__(self, auth: YS7Auth, cam: CameraInfo, rtsp_ip: str, rtsp_user: str, rtsp_pass: str):
        super().__init__()
        self.auth = auth
        self.cam = cam
        self.ptz = PTZController(auth, cam)
        self.rtsp_ip = rtsp_ip
        self.rtsp_user = rtsp_user or "admin"
        self.rtsp_pass = rtsp_pass
        self._live_url = None

        info = self._device_info()
        print(f"\n✅ 已连接: {cam.name}")
        print(f"   序列号: {cam.serial}")
        if info:
            print(f"   型号: {info.get('model', '?')}  固件: {info.get('firmware', '?')}")

    def _device_info(self) -> dict:
        import requests
        r = requests.post(
            "https://open.ys7.com/api/lapp/device/info",
            data={"accessToken": self.auth.get_access_token(), "deviceSerial": self.cam.serial},
        )
        data = r.json()
        return data.get("data", {}) if data.get("code") == "200" else {}

    def do_up(self, _):       print("⬆", "OK" if self.ptz.move("up") else "FAIL")
    def do_down(self, _):     print("⬇", "OK" if self.ptz.move("down") else "FAIL")
    def do_left(self, _):     print("⬅", "OK" if self.ptz.move("left") else "FAIL")
    def do_right(self, _):    print("➡", "OK" if self.ptz.move("right") else "FAIL")
    def do_ul(self, _):       print("↖", "OK" if self.ptz.move("up_left") else "FAIL")
    def do_ur(self, _):       print("↗", "OK" if self.ptz.move("up_right") else "FAIL")
    def do_dl(self, _):       print("↙", "OK" if self.ptz.move("down_left") else "FAIL")
    def do_dr(self, _):       print("↘", "OK" if self.ptz.move("down_right") else "FAIL")
    def do_stop(self, _):     print("✓" if self.ptz.stop() else "FAIL")

    def do_zoomin(self, _):
        print("🔍", "OK" if self.ptz.zoom_in() else "FAIL")
    def do_zoomout(self, _):
        print("🔎", "OK" if self.ptz.zoom_out() else "FAIL")

    def do_live(self, _):
        rtsp = get_rtsp_url(self.rtsp_ip, self.rtsp_user, self.rtsp_pass)
        print(f"🌐 RTSP:  {rtsp}")
        self._live_url = rtsp

    def do_live_cloud(self, _):
        flv = get_cloud_flv_url(self.auth, self.cam)
        hls = get_cloud_hls_url(self.auth, self.cam)
        if flv:
            print(f"☁️  FLV: {flv}")
            self._live_url = flv
        if hls:
            print(f"☁️  HLS: {hls}")
        if not flv and not hls:
            print("❌ 获取云端流失败")

    def do_ffplay(self, _):
        url = self._live_url or get_rtsp_url(self.rtsp_ip, self.rtsp_user, self.rtsp_pass)
        if not url:
            print("请先执行 live")
            return
        print("🎬 启动 ffplay...")
        subprocess.Popen(["ffplay", "-window_title", self.cam.name, url])

    def do_info(self, _):
        info = self._device_info()
        for k, v in info.items():
            print(f"  {k}: {v}")

    def do_snap(self, arg):
        url = self._live_url or get_rtsp_url(self.rtsp_ip, self.rtsp_user, self.rtsp_pass)
        path = arg.strip() or "/tmp/c6c_snap.jpg"
        result = take_snapshot(url, path)
        if result:
            print(f"📸 截图已保存: {result} ({os.path.getsize(result)} bytes)")
        else:
            print("❌ 截图失败")

    def do_capture(self, arg):
        path = arg.strip() or "/tmp/c6c_capture.jpg"
        print("📷 正在抓拍...")
        result = download_capture(self.auth, self.cam, path)
        if result:
            size = os.path.getsize(result)
            print(f"📸 抓拍已保存: {result} ({size} bytes)")
            if sys.platform == "darwin":
                subprocess.Popen(["qlmanage", "-p", result], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif sys.platform == "linux":
                subprocess.Popen(["xdg-open", result], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            print("❌ 抓拍失败")

    def do_onvif(self, _):
        rtsp = get_rtsp_url(self.rtsp_ip, self.rtsp_user, self.rtsp_pass)
        onvif.STREAM_URL = rtsp
        onvif.SNAPSHOT_URL = rtsp
        onvif.PTZ_CONTROLLER = self.ptz

        threading.Thread(target=self._start_onvif_server, daemon=True).start()
        print(f"🟢 ONVIF 已启动: http://{onvif.HOST_IP}:{onvif.ONVIF_PORT}/onvif/device_service")

    def _start_onvif_server(self):
        try:
            port = _pick_port(onvif.ONVIF_PORT)
        except OSError as e:
            print(f"❌ {e}")
            return
        if port != onvif.ONVIF_PORT:
            print(f"⚠️  端口 {onvif.ONVIF_PORT} 被占用，使用 {port}")
            onvif.ONVIF_PORT = port

        threading.Thread(target=lambda: discovery_run(
            onvif.UUID_URN, onvif.SCOPES, onvif.HOST_IP, port,
        ), daemon=True).start()
        onvif.start_mjpeg_relay_inline(onvif.STREAM_URL, onvif.MJPEG_PORT)
        try:
            onvif.start_onvif_server("0.0.0.0", port)
        except OSError as e:
            print(f"❌ ONVIF 启动失败: {e}")

    def do_exit(self, _):
        print("👋 Bye")
        return True
    do_quit = do_exit
    do_EOF = do_exit


def run_headless(auth: YS7Auth, cam: CameraInfo, config: dict):
    ptz = PTZController(auth, cam)

    stream_url = get_rtsp_url(config["camera_ip"], config["rtsp_user"], config["rtsp_pass"])
    if config["rtsp_pass"]:
        print(f"📹 RTSP: {stream_url}")
    else:
        cloud = get_cloud_flv_url(auth, cam)
        if cloud:
            stream_url = cloud
            print(f"☁️  云端流: {stream_url}")
        else:
            print("⚠️  未设置 RTSP 密码且云端流获取失败，视频不可用")

    try:
        actual_port = _pick_port(config["onvif_port"])
    except OSError as e:
        print(f"❌ {e}")
        sys.exit(1)

    if actual_port != config["onvif_port"]:
        print(f"⚠️  端口 {config['onvif_port']} 被占用，使用 {actual_port}")

    onvif.HOST_IP = config["host_ip"]
    onvif.ONVIF_PORT = actual_port
    onvif.MJPEG_PORT = config["mjpeg_port"]
    onvif.STREAM_URL = stream_url
    onvif.SNAPSHOT_URL = stream_url
    onvif.PTZ_CONTROLLER = ptz
    onvif.DEVICE_UUID = str(onvif.uuid.uuid4())
    onvif.UUID_URN = f"urn:uuid:{onvif.DEVICE_UUID}"

    mjpeg_proc = onvif.start_mjpeg_relay_inline(stream_url, config["mjpeg_port"])

    threading.Thread(
        target=lambda: discovery_run(
            onvif.UUID_URN, onvif.SCOPES, config["host_ip"], actual_port,
        ),
        daemon=True,
    ).start()

    print(f"🟢 ONVIF 服务已启动 ({cam.name})")
    print(f"   MJPEG: http://{config['host_ip']}:{config['mjpeg_port']}/stream")
    print(f"   ONVIF: http://{config['host_ip']}:{actual_port}/onvif/device_service")
    print(f"   如果自动发现不工作，请在 ONVIF 客户端中手动添加上述地址")

    try:
        onvif.start_onvif_server("0.0.0.0", actual_port)
    except KeyboardInterrupt:
        print("\n🛑 正在停止...")
    except OSError as e:
        print(f"❌ ONVIF 启动失败: {e}")
        sys.exit(1)
    finally:
        stop_mjpeg_relay(mjpeg_proc)


def parse_args():
    p = argparse.ArgumentParser(description="萤石 C6C ONVIF Adapter")
    p.add_argument("--app-key", default=os.getenv("YS7_APP_KEY"), help="萤石开放平台 AppKey")
    p.add_argument("--app-secret", default=os.getenv("YS7_APP_SECRET"), help="萤石开放平台 AppSecret")
    p.add_argument("--camera-ip", default=os.getenv("CAMERA_IP", "192.168.1.100"), help="摄像机 IP (RTSP 用)")
    p.add_argument("--rtsp-user", default=os.getenv("RTSP_USER", "admin"), help="RTSP 用户名")
    p.add_argument("--rtsp-pass", default=os.getenv("RTSP_PASS", ""), help="RTSP 密码 (验证码)")
    p.add_argument("--host-ip", default=None, help="ONVIF 对外 IP")
    p.add_argument("--onvif-port", type=int, default=int(os.getenv("ONVIF_PORT", "8089")))
    p.add_argument("--mjpeg-port", type=int, default=int(os.getenv("MJPEG_PORT", "8555")))
    p.add_argument("--interactive", "-i", action="store_true", help="交互模式 (带 PTZ 控制)")
    p.add_argument("--camera-serial", default=None, help="指定设备序列号 (多设备时)")
    return p.parse_args()


def main():
    args = parse_args()

    if not args.app_key or not args.app_secret:
        print("❌ 需要设置萤石开放平台凭据:")
        print("   export YS7_APP_KEY=your_app_key")
        print("   export YS7_APP_SECRET=your_app_secret")
        print("   或: --app-key KEY --app-secret SECRET")
        print()
        print("   注册地址: https://open.ys7.com")
        sys.exit(1)

    auth = YS7Auth(args.app_key, args.app_secret)

    try:
        token = auth.get_access_token()
        print(f"✅ 萤石云认证成功 (token: {token[:16]}...)")
    except RuntimeError as e:
        print(f"❌ {e}")
        sys.exit(1)

    cameras = auth.get_cameras()
    if not cameras:
        print("❌ 没有绑定设备，请先在萤石开放平台添加设备")
        sys.exit(1)

    cam = cameras[0]
    if args.camera_serial:
        match = [c for c in cameras if c.serial == args.camera_serial]
        if not match:
            print(f"❌ 未找到设备 {args.camera_serial}")
            print(f"   可用设备: {', '.join(c.serial for c in cameras)}")
            sys.exit(1)
        cam = match[0]
    elif len(cameras) > 1:
        print(f"发现 {len(cameras)} 个设备:")
        for i, c in enumerate(cameras):
            print(f"  {i}: {c.name} ({c.serial})")
        print("  用 --camera-serial 指定")

    host_ip = args.host_ip or detect_host_ip()
    config = {
        "camera_ip": args.camera_ip, "rtsp_user": args.rtsp_user, "rtsp_pass": args.rtsp_pass,
        "host_ip": host_ip, "onvif_port": args.onvif_port, "mjpeg_port": args.mjpeg_port,
    }

    if args.interactive:
        CameraConsole(auth, cam, args.camera_ip, args.rtsp_user, args.rtsp_pass).cmdloop()
    else:
        run_headless(auth, cam, config)


if __name__ == "__main__":
    main()
