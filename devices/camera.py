import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional
import time
import json

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

        self._connected = False
        self._scanning = False
        self._health = DeviceHealth(
            device_id=str(camera_id),
            device_type="camera",
            status=DeviceStatus.OFFLINE,
            last_heartbeat_ms=None,
            message=""
        )
        self._last_heartbeat_ms = 0

        self.event_bus = event_bus

        self._fetch_task: Optional[asyncio.Task] = None

        self.host = get_config("camera.host", "192.168.1.79")
        self.port = get_config("camera.port", 1024)
        self.trigger_port = get_config("camera.trigger_port", 1025)
        self.timeout = get_config("camera.timeout", 3.0)

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

    @abstractmethod
    async def connect(self) -> None:
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        pass

    @abstractmethod
    async def start_scan_session(self) -> None:
        pass

    @abstractmethod
    async def stop_scan_session(self) -> None:
        pass

    @abstractmethod
    async def fetch_loop(self) -> None:
        pass

    def get_health(self) -> DeviceHealth:
        return self._health

    async def _publish_result(self, result: CameraResult) -> None:
        if not result.symbology == "UNKNOWN":
            print(f" ts_ms: {result.ts_ms}")
            self.event_bus.emit(
                event_type=EventType.CAMERA_RESULT,
                source=f"camera_{self.camera_id}",
                payload={
                    "camera_id": self.camera_id,
                    "result": result.result,
                    "code": result.code,
                    "symbology": result.symbology,
                    "ts_ms": result.ts_ms
                }
            )

    def _update_health(self, status: DeviceStatus, message: str = ""):
        now_ms = time.time_ns() // 1_000_000

        self._health.status = status
        self._health.message = message
        self._health.last_heartbeat_ms = now_ms
        self._last_heartbeat_ms = now_ms

        self.event_bus.emit(
            event_type=EventType.DEVICE_FAULT if status != DeviceStatus.ONLINE else EventType.CAMERA_HEARTBEAT,
            source=f"camera_{self.camera_id}",
            payload={
                "device_id": self.camera_id,
                "device_type": "camera",
                "status": status.value,
                "message": message,
                "timestamp": now_ms
            }
        )

def _parse_to_camera_result(data: dict) -> CameraResult:
    return CameraResult(
        camera_id=1,
        result=data.get("result", "FALSE"),
        code=data.get("code", ""),
        symbology=data.get("symbology", ""),
        ts_ms=time.time_ns() / 1_000_000 - 100 * float(data.get("camera_delay")),
    )

class OptCameraClient(BaseCameraClient):
    async def connect(self) -> None:
        try:
            self.logger.info(f"[CAM{self.camera_id}] 连接 {self.host}:{self.port}")
            self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
            self._connected = True
            self._update_health(DeviceStatus.ONLINE, "connected")
            self.logger.info(f"[CAM{self.camera_id}] 连接成功")
        except Exception as e:
            self._connected = False
            self._update_health(DeviceStatus.OFFLINE, str(e))
            self.logger.error(f"连接失败: {e}")
            raise

    async def disconnect(self) -> None:
        self._scanning = False
        if self._fetch_task:
            self._fetch_task.cancel()
            try:
                await self._fetch_task
            except asyncio.CancelledError:
                pass

        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()

        self._connected = False
        self._update_health(DeviceStatus.OFFLINE, "disconnected")

    async def start_scan_session(self) -> None:
        if not self._connected:
            raise RuntimeError("未连接")
        if self._scanning:
            return

        await self._async_trigger("start")
        self._scanning = True
        self._fetch_task = asyncio.create_task(self.fetch_loop())
        self.logger.info(f"[CAM{self.camera_id}] 扫码会话已启动")

    async def stop_scan_session(self) -> None:
        self._scanning = False
        await self._async_trigger("stop")
        self.logger.info(f"[CAM{self.camera_id}] 已停止扫码")

    async def fetch_loop(self):
        buffer = b""
        while self._scanning and self._connected:
            try:
                buffer = b""
                while True:
                    chunk = await self._reader.read(1024)
                    if not chunk:
                        raise ConnectionError("连接断开")
                    buffer += chunk
                    if b"}" in chunk:  # JSON 结束符
                        break
                try:
                    data = json.loads(buffer.decode().strip())
                    await self._handle_message(data)
                except json.JSONDecodeError:
                    self.logger.error("Json Failed ...")
                    continue

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                self.logger.error(f"读循环异常: {e}")
                await asyncio.sleep(0.1)
                break

        self._scanning = False

    async def _handle_message(self, data: dict):
        self._update_health(DeviceStatus.ONLINE, "receiving data")
        res = _parse_to_camera_result(data)
        await self._publish_result(res)

    # ====================== 异步触发（修复卡死） ======================
    async def _async_trigger(self, cmd: str):
        try:
            reader, writer = await asyncio.open_connection(self.host, self.trigger_port)
            writer.write(cmd.encode())
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            self.logger.info(f"触发指令发送: {cmd}")
        except Exception as e:
            self.logger.error(f"触发失败: {e}")

    def is_scanning(self):
        return self._scanning

    def is_connected(self):
        return self._connected


if __name__ == "__main__":
    import socket
    import time
    import json

    def main():
        CAMERA_IP = "192.168.1.79"
        PORT_TRIGGER = 1025  # 触发端口
        PORT_READ = 1024  # 读码端口
        TRIGGER_CMD = b"start"
        STOP_CMD = b"stop"

        # 日志文件
        LOG_FILE = "log.txt"
        # 条码完整规则：1→2→3→4→5→6 循环
        # =================================================================

        # 1. 先发送一次触发指令
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((CAMERA_IP, PORT_TRIGGER))
                s.sendall(TRIGGER_CMD)
                print("✅ 已发送拍照触发指令")
        except Exception as e:
            print(f"❌ 触发失败: {e}")

        # 全局状态
        my_str = ""
        pre_code = "-1"
        print("开始读取条码...\n")

        # 2. 长连接读取（不再反复断开重连，超级稳定）
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s_read:
                s_read.settimeout(5)
                s_read.connect((CAMERA_IP, PORT_READ))
                print(f"✅ 已连接读码端口 {PORT_READ}，持续监听中...\n")

                while True:
                    try:
                        # 【关键修复】接收完整数据，解决分段/粘包
                        buffer = b""
                        while True:
                            chunk = s_read.recv(1024)
                            if not chunk:
                                raise ConnectionError("连接断开")
                            buffer += chunk
                            if b"}" in chunk:  # JSON 结束符
                                break

                        # 解析
                        data = json.loads(buffer.decode().strip())
                        print(f"data: {data}")
                        code = data.get("code", "").strip()
                        if not code or code == "NG":
                            continue

                        print(f"当前读到: {code}, 时间: {time.time_ns() / 1_000_000}")

                        # ==================== 核心逻辑：稳定拼接条码 ====================
                        my_str += code

                        # 规则：数字变小 = 新一轮条码开始 → 写入日志
                        if code.isdigit() and pre_code.isdigit():
                            if int(code) < int(pre_code):
                                # 写入前清理：去掉最后一个（新开始的字符）
                                save_str = my_str[:-1]
# 2017-08-04 18:55:21.087
                                # 只保存有效内容
                                if save_str:
                                    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                                    log_line = f"{timestamp}    {save_str}\n"

                                    with open(LOG_FILE, "a", encoding="utf-8") as f:
                                        f.write(log_line)

                                    print(f"\n📝 已保存完整条码: {save_str}")

                                # 重置：保留最后一个字符作为新一轮开始
                                my_str = code

                        # 更新上一个值
                        pre_code = code

                    except socket.timeout:
                        continue
                    except json.JSONDecodeError:
                        continue
                    except Exception as e:
                        print(f"读取异常: {e}")
                        continue

        except Exception as e:
            print(f"连接异常: {e}")

    main()

# 1774833950987
# 1774833951097
# 1774833955357.524