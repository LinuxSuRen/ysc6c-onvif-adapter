# 萤石 C6C ONVIF Adapter

将萤石 C6C (CS-CV246) 云台摄像机暴露为标准 ONVIF 设备。通过萤石开放平台 API 实现 PTZ 控制和视频流中继。

## 为什么

萤石 C6C 是纯云端摄像机 — 端口 8000 使用 Hikvision SDK 二进制协议（非 HTTP/ISAPI），无 ONVIF 支持。标准 NVR/监控软件无法直接接入。

此 Adapter 将萤石云端 API 转换为标准 ONVIF 协议：

- ONVIF WS-Discovery 自动发现
- ONVIF PTZ 云台控制
- MJPEG / HLS 视频流
- 云端抓拍截图

## 前置条件

1. **注册萤石开放平台**：https://open.ys7.com → 创建应用 → 获取 AppKey 和 AppSecret
2. **绑定设备**：在开放平台中添加你的 C6C 摄像机（需要设备序列号）
3. （可选）在萤石 App 中开启 RTSP：设置 → 本地设备设置 → 高级 → RTSP

## 快速开始

### 安装

```bash
pip install -r requirements.txt
```

### 无头模式（直接启动 ONVIF 服务）

```bash
python3 main.py \
  --app-key your_app_key \
  --app-secret your_app_secret \
  --host-ip 192.168.1.100
```

或通过环境变量：

```bash
export YS7_APP_KEY=your_app_key
export YS7_APP_SECRET=your_app_secret
python3 main.py
```

### 交互模式（带 PTZ 控制台）

```bash
python3 main.py --app-key KEY --app-secret SECRET -i
```

交互命令：

```
📷> up          ⬆ 向上转动
📷> down        ⬇ 向下转动
📷> left        ⬅ 向左转动
📷> right       ➡ 向右转动
📷> stop        停止转动
📷> zoomin      🔍 放大
📷> zoomout     🔎 缩小
📷> live        显示 RTSP 地址
📷> live_cloud  显示云端流地址
📷> ffplay      用 ffplay 播放
📷> capture     云端抓拍并预览
📷> snap        RTSP 截图
📷> info        设备信息
📷> onvif       启动 ONVIF 服务
📷> exit        退出
```

## ONVIF 协议栈

| 协议 | 实现 |
|------|------|
| WS-Discovery | UDP 3702 组播 |
| Device Service | GetDeviceInformation, GetServices, GetScopes |
| Media Service | GetProfiles, GetStreamUri, GetSnapshotUri |
| PTZ Service | ContinuousMove, Stop, GetNodes, GetConfigurations |

启动后在局域网内任意 ONVIF 客户端中自动发现。

## API

```python
from auth import YS7Auth
from ptz import PTZController
from stream import get_cloud_flv_url, download_capture

auth = YS7Auth("app_key", "app_secret")
auth.get_access_token()

cameras = auth.get_cameras()
cam = cameras[0]

ptz = PTZController(auth, cam)
ptz.move("up")
ptz.move("left")
ptz.stop()

url = get_cloud_flv_url(auth, cam)
download_capture(auth, cam, "/tmp/snap.jpg")
```

## Docker

```bash
docker compose up -d
```

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `YS7_APP_KEY` | — | 萤石开放平台 AppKey（必需） |
| `YS7_APP_SECRET` | — | 萤石开放平台 AppSecret（必需） |
| `CAMERA_IP` | 192.168.1.100 | 摄像机 IP（RTSP 用） |
| `RTSP_USER` | admin | RTSP 用户名 |
| `RTSP_PASS` | — | RTSP 密码（验证码） |
| `HOST_IP` | 自动检测 | ONVIF 服务对外 IP |
| `ONVIF_PORT` | 8089 | ONVIF 服务端口 |
| `MJPEG_PORT` | 8555 | MJPEG 流端口 |

## 命令行参数

```
python3 main.py [选项]

选项:
  --app-key KEY        萤石开放平台 AppKey
  --app-secret SECRET  萤石开放平台 AppSecret
  --camera-ip IP       摄像机 IP (RTSP 用)
  --rtsp-user USER     RTSP 用户名
  --rtsp-pass PASS     RTSP 密码
  --host-ip IP         ONVIF 对外 IP (自动检测)
  --onvif-port PORT    ONVIF 服务端口 (8089)
  --mjpeg-port PORT    MJPEG 流端口 (8555)
  -i, --interactive    交互模式 (带 PTZ 控制)
  --camera-serial ID   指定设备序列号 (多设备时)
```

## 架构

```
ysc6c-onvif-adapter/
├── main.py                  # 入口：无头模式 + 交互 CLI
├── auth.py                  # 萤石云 API 认证 (appKey/appSecret → accessToken)
├── ptz.py                   # 云台控制 (POST open.ys7.com/api/lapp/device/ptz/*)
├── stream.py                # 直播流 + 云端抓拍
├── onvif/
│   ├── __init__.py          # ONVIF SOAP HTTP 服务器 (Device/Media/PTZ)
│   └── discovery.py         # WS-Discovery 组播响应器
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

## 技术说明

C6C 的端口 8000 不走 HTTP/ISAPI，而是 Hikvision HCNetSDK 二进制协议。因此 PTZ 控制通过**萤石开放平台 REST API** 实现，视频流通过 RTSP（需在 App 中开启）或云端 FLV/HLS 中继。

## 致谢

- [BaQs/pyEzviz](https://github.com/BaQs/pyEzviz) · [RenierM26/pyEzvizApi](https://github.com/RenierM26/pyEzvizApi)
- 萤石开放平台 https://open.ys7.com
