"""萤石 C6C — 萤石云 API 认证模块"""

import time
from dataclasses import dataclass
from typing import Optional

import requests

YS7_API = "https://open.ys7.com"


@dataclass
class CameraInfo:
    serial: str
    name: str
    channel_no: int = 1


class YS7Auth:
    def __init__(self, app_key: str, app_secret: str):
        self.app_key = app_key
        self.app_secret = app_secret
        self.access_token: Optional[str] = None
        self.expire_time: float = 0

    def get_access_token(self) -> str:
        if self.access_token and time.time() < self.expire_time - 60:
            return self.access_token

        r = requests.post(
            f"{YS7_API}/api/lapp/token/get",
            data={"appKey": self.app_key, "appSecret": self.app_secret},
        )
        data = r.json()
        if data.get("code") != "200":
            raise RuntimeError(f"获取 accessToken 失败: {data}")

        self.access_token = data["data"]["accessToken"]
        self.expire_time = float(data["data"]["expireTime"]) / 1000
        return self.access_token

    def get_cameras(self) -> list[CameraInfo]:
        token = self.get_access_token()
        r = requests.post(
            f"{YS7_API}/api/lapp/device/list",
            data={"accessToken": token},
        )
        data = r.json()
        if data.get("code") != "200":
            return []
        return [
            CameraInfo(
                serial=d["deviceSerial"],
                name=d.get("deviceName", d["deviceSerial"]),
                channel_no=d.get("channelNo", 1),
            )
            for d in data.get("data", [])
        ]
