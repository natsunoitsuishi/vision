# devices/camera/base.py
import asyncio
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from config.manager import get_config
from domain.enums import DeviceStatus, EventType
from domain.models import DeviceHealth, CameraResult
from services.event_bus import EventBus


class BaseCameraClient(ABC):
    """相机客户端基类"""

    def __init__(self, camera_id: int, event_bus: EventBus):
        self.camera_id = camera_id
        self.config = get_config()
        self.logger = logging.getLogger(f"camera.{camera_id}")


        # 状态
        self._connected = False
        self._scanning = False
        self._health = DeviceHealth(
            device_id=str(camera_id),
            device_type="camera",
            status=DeviceStatus.OFFLINE,
            last_heartbeat_ts=None,
            message=""
        )
        self._last_heartbeat = 0

        # # 回调
        # self._result_callback: Optional[Callable[[CameraResult], Awaitable[None]]] = None


        self.event_bus = event_bus  # ← 只依赖事件总线

        # 任务
        self._fetch_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None

        # 连接参数（适配设备模拟程序）
        self.host = get_config("camera.host", "192.168.10.79")
        self.port = get_config("camera.port", "1024")
        self.timeout = get_config("camera.timeout", 3.0)

        # TCP Socket（用于asyncio）
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

    @abstractmethod
    async def connect(self) -> None:
        """建立TCP连接"""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """断开TCP连接"""
        pass

    @abstractmethod
    async def start_scan_session(self) -> None:
        """启动持续扫码会话"""
        pass

    @abstractmethod
    async def stop_scan_session(self) -> None:
        """停止持续扫码会话"""
        pass

    @abstractmethod
    async def fetch_loop(self) -> None:
        """持续接收结果的循环"""
        pass

    def get_health(self) -> DeviceHealth:
        """获取健康状态"""
        return self._health


    async def _publish_result(self, result: CameraResult) -> None:
       self.logger.info(f"[CAM{self.camera_id}] 发布结果事件: {result}")

       # 发布相机结果事件
       self.event_bus.emit(
           event_type=EventType.CAMERA_RESULT,
           source=f"camera_{self.camera_id}",
           payload={
               "camera_id": self.camera_id,
               "result": result.result,
               "code": result.code,
               "symbology": result.symbology,
               "ts_ms": result.ts_ms,
               "raw_data": result.raw_data if hasattr(result, 'raw_data') else None,
               "timestamp": datetime.now().timestamp()
           }
       )


    def _update_health(self, status: DeviceStatus, message: str = ""):
        """更新健康状态"""
        if self._health.status != status:
            self._health.status = status
            self._health.message = message
            self._health.last_heartbeat_ts = datetime.now().timestamp()
            self.logger.info(f"[CAM{self.camera_id}] 状态 -> {status.value} {message}")

            # 发布设备状态变化事件
            self.event_bus.emit(
                event_type=EventType.DEVICE_FAULT if status != DeviceStatus.ONLINE else EventType.CAMERA_HEARTBEAT,
                source=f"camera_{self.camera_id}",
                payload={
                    "device_id": self.camera_id,
                    "device_type": "camera",
                    "status": status.value,
                    "message": message,
                    "timestamp": datetime.now().timestamp()
                }
            )

class OptCameraClient(BaseCameraClient):
    """对接设备模拟程序的相机客户端（TCP长连接 + JSON行协议）"""

    async def connect(self) -> None:
        """建立TCP连接"""
        try:
            self.logger.info(f"[CAM{self.camera_id}] 连接 {self.host}:{self.port}")

            self._reader, self._writer = await asyncio.open_connection(
                self.host, self.port
            )

            self._connected = True
            self._update_health(DeviceStatus.ONLINE, "connected")

            self.logger.info(f"[CAM{self.camera_id}] 连接成功")

        except Exception as e:
            self._connected = False
            self._update_health(DeviceStatus.OFFLINE, str(e))
            self.logger.error(f"[CAM{self.camera_id}] 连接失败: {e}")
            raise

    async def disconnect(self) -> None:
        """断开TCP连接"""
        self._scanning = False

        if self._fetch_task and not self._fetch_task.done():
            self._fetch_task.cancel()
            try:
                await self._fetch_task
            except asyncio.CancelledError:
                pass
            self._fetch_task = None

        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()

        self._connected = False
        self._update_health(DeviceStatus.OFFLINE, "disconnected")

        self.logger.info(f"[CAM{self.camera_id}] 已断开")

    async def start_scan_session(self) -> None:
        """启动持续扫码会话"""
        if not self._connected:
            raise RuntimeError(f"相机{self.camera_id} 未连接，无法启动会话")

        if self._scanning:
            self.logger.info(f"[CAM{self.camera_id}] 接收循环已在运行")
            return

        self._scanning = True
        self._fetch_task = asyncio.create_task(self.fetch_loop())
        self.logger.info(f"[CAM{self.camera_id}] 开始接收")

    async def stop_scan_session(self) -> None:
        """停止持续扫码会话"""
        self._scanning = False

        if self._fetch_task and not self._fetch_task.done():
            self._fetch_task.cancel()
            try:
                await self._fetch_task
            except asyncio.CancelledError:
                pass
            self._fetch_task = None

        self.logger.info(f"[CAM{self.camera_id}] 停止接收")

    async def fetch_loop(self) -> None:
        """持续接收设备模拟程序推送的扫码结果"""
        buffer = b''

        while self._scanning and self._connected:
            try:
                # print("Hello, ")
                # 接收数据（带超时）
                data = await asyncio.wait_for(
                    self._reader.read(4096),
                    timeout=1.0
                )

                if not data:
                    # 连接已关闭
                    self.logger.warning(f"[CAM{self.camera_id}] 连接关闭")
                    self._connected = False
                    self._update_health(DeviceStatus.OFFLINE, "连接关闭")
                    break

                buffer += data

                # 按换行符分割消息
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    if line:
                        # 解析单条JSON消息
                        await self._handle_message(line)

            except asyncio.TimeoutError:
                # 超时是正常的，继续等待
                continue
            except asyncio.CancelledError:
                self.logger.info(f"[CAM{self.camera_id}] 接收循环被取消")
                break
            except Exception as e:
                self.logger.error(f"[CAM{self.camera_id}] 接收异常: {e}")
                await asyncio.sleep(0.1)

                # 如果是连接错误，标记断开
                if "Connection" in str(e) or "Broken pipe" in str(e):
                    self._connected = False
                    self._update_health(DeviceStatus.OFFLINE, f"连接异常: {e}")
                    break

        # 循环结束，清理状态
        self._scanning = False
        if not self._connected:
            self.logger.warning(f"[CAM{self.camera_id}] 接收循环结束，需要重新连接")

    async def _handle_message(self, line: bytes) -> None:
        """处理单条JSON消息"""
        try:
            # 解析JSON
            data = json.loads(line.decode('utf-8'))

            # 验证消息类型
            if data.get("type") != "scan_result":
                self.logger.warning(f"[CAM{self.camera_id}] 收到未知消息类型: {data.get('type')}")
                return

            # 更新心跳时间
            self._last_heartbeat = datetime.now().timestamp()

            # 转换为CameraResult
            camera_result = self._parse_to_camera_result(data)

            # print("Hello, World !!")
            # # 通过事件总线发布结果（替代回调）
            await self._publish_result(camera_result)

            # 打印接收到的结果
            self.logger.info(f"[CAM{self.camera_id}] 收到结果: {camera_result}")

        except json.JSONDecodeError as e:
            self.logger.error(f"[CAM{self.camera_id}] JSON解析失败: {e}, 原始数据: {line}")
        except Exception as e:
            self.logger.error(f"[CAM{self.camera_id}] 处理失败: {e}", exc_info=True)

    def _parse_to_camera_result(self, data: dict) -> CameraResult:
        """解析设备模拟程序推送的JSON数据为CameraResult对象

        设备模拟程序JSON格式：
        {
            "type": "scan_result",
            "camera_id": "CAM1",
            "result": "OK",
            "code": "QR-001",
            "symbology": "QR",
            "ts_ms": 230
        }
        """
        # 解析camera_id字符串为整数 ("CAM1" -> 1, "CAM2" -> 2)
        camera_id_str = data.get("camera_id", f"CAM{self.camera_id}")
        if camera_id_str.startswith("CAM"):
            camera_id = int(camera_id_str[3:])
        else:
            camera_id = int(camera_id_str)

        # 提取字段
        result_str = data.get("result", "NG")
        code = data.get("code")
        symbology = data.get("symbology")
        ts_ms = data.get("ts_ms", 0)

        # 创建CameraResult对象
        return CameraResult(
            camera_id=camera_id,
            result=result_str,
            code=code,
            symbology=symbology,
            ts_ms=ts_ms,
            type=data.get("type", "scan_result")
        )

    def is_scanning(self) -> bool:
        return self._scanning
    def is_connected(self) -> bool:
        return self._connected


if __name__ == "__main__":

    import socket
    import time

    # 相机配置（与截图一致）
    CAMERA_IP = "192.168.10.79"
    CAMERA_PORT = 1025
    TRIGGER_CMD = b"start"  # 触发拍照指令
    STOP_CMD = b"stop"  # 停止触发指令

    def tcp_trigger_capture_on():
        try:
            # 创建TCP客户端套接字
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((CAMERA_IP, CAMERA_PORT))
                print(f"已连接到相机: {CAMERA_IP}:{CAMERA_PORT}")

                # 发送触发指令拍照
                s.sendall(TRIGGER_CMD)
                print("已发送触发指令: start")

                # 可选：等待拍照完成，再发送停止指令
                # time.sleep(1)
                # s.sendall(STOP_CMD)
                # print("已发送停止指令: stop")

        except Exception as e:
            print(f"触发失败: {e}")

    def tcp_trigger_capture_off():
        try:
            # 创建TCP客户端套接字
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((CAMERA_IP, CAMERA_PORT))
                print(f"已连接到相机: {CAMERA_IP}:{CAMERA_PORT}")

                # 发送触发指令拍照
                s.sendall(STOP_CMD)
                print("已发送触发指令: stop")

                # 可选：等待拍照完成，再发送停止指令
                # time.sleep(1)
                # s.sendall(STOP_CMD)
                # print("已发送停止指令: stop")

        except Exception as e:
            print(f"触发失败: {e}")

    tcp_trigger_capture_on()

    import socket

    # 目标 IP 和端口
    ip = "192.168.10.79"
    port = 1024
    my_str = ''
    pre_code = '-1'
    while True:
        # 创建 TCP 连接
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)

        try:
            # 连接
            s.connect((ip, port))

            # 发送数据（HTTP 协议最小请求）
            s.send(b"GET / HTTP/1.1\r\nHost: test\r\n\r\n")

            # 接收回复
            reply = s.recv(4096)
            import json

            data = json.loads(reply.decode())
            code = data.get("code")
            print(code)

            if not code == 'NG':
                my_str += code

                print(f"code: {code}, pre_code: {pre_code}")
                if int(code) < int(pre_code):
                    with open("log.txt", "a") as f:
                        f.write(my_str + "\n")
                        my_str = ''

                pre_code = code

        except Exception as e:
            print("失败:", e)
        finally:
            s.close()