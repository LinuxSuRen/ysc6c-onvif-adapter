"""萤石 C6C 云台控制 — 萤石云 API"""

import time as _time
import requests
from auth import YS7Auth, CameraInfo


class PTZController:
    DIRECTIONS = {
        "up": "0", "down": "1", "left": "2", "right": "3",
        "up_left": "4", "down_left": "5", "up_right": "6", "down_right": "7",
        "zoom_in": "8", "zoom_out": "9",
    }

    def __init__(self, auth: YS7Auth, cam: CameraInfo):
        self.auth = auth
        self.cam = cam
        self._moving = False

    def _start(self, direction: str, speed: int = 1):
        r = requests.post(
            "https://open.ys7.com/api/lapp/device/ptz/start",
            data={
                "accessToken": self.auth.get_access_token(),
                "deviceSerial": self.cam.serial,
                "channelNo": str(self.cam.channel_no),
                "direction": self.DIRECTIONS[direction],
                "speed": str(speed),
            },
        )
        result = r.json()
        ok = result.get("code") == "200"
        if not ok:
            print(f"  PTZ API: {result.get('msg', result)}")
        return ok

    def _stop(self):
        r = requests.post(
            "https://open.ys7.com/api/lapp/device/ptz/stop",
            data={
                "accessToken": self.auth.get_access_token(),
                "deviceSerial": self.cam.serial,
                "channelNo": str(self.cam.channel_no),
            },
        )
        return r.json().get("code") == "200"

    def move(self, direction: str, duration_ms: int = 500):
        if self._moving:
            self._stop()
            _time.sleep(0.2)
        self._moving = True
        ok = self._start(direction)
        if ok:
            _time.sleep(duration_ms / 1000)
            self._stop()
        self._moving = False
        return ok

    def stop(self):
        self._moving = False
        return self._stop()

    def zoom_in(self, duration_ms: int = 300):
        return self.move("zoom_in", duration_ms)

    def zoom_out(self, duration_ms: int = 300):
        return self.move("zoom_out", duration_ms)
