import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional
import time
import json

from pymodbus.client import AsyncModbusTcpClient

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

        self.host = get_config("camera.host")
        self.port = get_config("camera.read_port")
        self.trigger_port = get_config("camera.trigger_port")
        self.timeout = get_config("camera.timeout")

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
        now_ms = time.time_ns() / 1_000_000

        self._health.status = status
        self._health.message = message
        self._health.last_heartbeat_ms = now_ms
        self._last_heartbeat_ms = now_ms

        if status != DeviceStatus.ONLINE:
            self.event_bus.emit(
                event_type=EventType.DEVICE_FAULT,
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
        ts_ms=time.time_ns() / 1_000_000 - get_config("camera.delay"),
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
                    if b"}" in chunk:
                        break
                try:
                    data = json.loads(buffer.decode().strip())
                    await self._handle_message(data)
                except json.JSONDecodeError:
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

# 运行验证
# if __name__ == "__main__":
#     import asyncio
#     from pymodbus.client import AsyncModbusTcpClient
#
#
#     class OptCameraHardwareTrigger:
#         def __init__(self, reader_ip: str = "192.168.1.79", port: int = 512, device_id = 1):
#             self.reader_ip = reader_ip
#             self.port = port
#             self.device_id = device_id  # 新版 pymodbus 用 device_id，不是 slave
#             self.client = None
#
#         async def connect(self):
#             """建立 ModbusTCP 连接"""
#             if self.client is None:
#                 self.client = AsyncModbusTcpClient(
#                     host=self.reader_ip,
#                     port=self.port,
#                     timeout=3
#                 )
#             if not self.client.connected:
#                 await self.client.connect()
#             return self.client.connected
#
#         async def wait_for_trigger(self):
#             """阻塞等待硬件触发（DI_0 上升沿）"""
#             if not await self.connect():
#                 print("❌ 读码器连接失败")
#                 return None
#
#             # 循环读取触发状态寄存器（地址 0x0200，参考 OPT 协议手册）
#             while True:
#                 try:
#                     # 读触发状态：0x01 = 已触发，0x00 = 未触发
#                     resp = await self.client.read_holding_registers(
#                         address=0x0200,
#                         count=1,
#                         device_id=self.device_id
#                     )
#
#                     print(resp)
#                     if resp.isError():
#                         await asyncio.sleep(0.01)
#                         continue
#
#                     trigger_flag = resp.registers[0]
#                     if trigger_flag == 0x01:
#                         # 触发成功 → 读取解码结果
#                         result = await self.read_decode_result()
#                         # 重置触发状态（写 0x00 清标志）
#                         await self.client.write_register(
#                             address=0x0200,
#                             value=0x00,
#                             device_id=self.device_id
#                         )
#                         return result
#                 except Exception as e:
#                     print(f"⚠️ 监控异常: {e}")
#                 await asyncio.sleep(0.01)  # 10ms 轮询一次
#
#         async def read_decode_result(self):
#             """读取触发后的解码结果（地址 0x0300）"""
#             resp = await self.client.read_holding_registers(
#                 address=0x0300,
#                 count=20,  # 足够存条码
#                 device_id=self.device_id
#             )
#             if resp.isError():
#                 return None
#
#             regs = resp.registers
#             success = regs[0] == 0x01  # 0x01 = 解码成功
#             code_len = regs[1]  # 条码字节长度
#
#             # 拼接条码内容（每个寄存器 2 字节，大端序）
#             code_bytes = b""
#             for i in range(2, 2 + (code_len + 1) // 2):
#                 if i >= len(regs):
#                     break
#                 code_bytes += regs[i].to_bytes(2, byteorder="big")
#
#             code = code_bytes.decode("utf-8", errors="ignore").strip("\x00")
#             return {
#                 "success": success,
#                 "code": code,
#                 "length": code_len
#             }
#
#         async def close(self):
#             """关闭连接"""
#             if self.client and self.client.connected:
#                 await self.client.close()
#
# # ------------------------------
# # 测试入口
# # ------------------------------
# async def main():
#     trigger = OptCameraHardwareTrigger(reader_ip="192.168.1.79")
#     print("📡 等待硬件触发（DI_0 上升沿）...")
#     try:
#         while True:
#             result = await trigger.wait_for_trigger()
#             print(result)
#             if result:
#                 if result["success"]:
#                     print(f"✅ 解码成功: {result['code']}")
#                 else:
#                     print("❌ 触发成功但解码失败")
#     except KeyboardInterrupt:
#         print("\n🛑 停止监控")
#     finally:
#         await trigger.close()
#
# if __name__ == "__main__":
#     asyncio.run(main())

if __name__ == "__main__":
    import socket
    import time
    import json

    def main():
        camera_ip = "192.168.1.79"
        port_trigger    = 1025     # 触发端口
        port_read       = 1024        # 读码端口
        trigger_cmd = b"start"

        stop_cmd = b"stop"
        log_file = "log.txt"

        # 日志文件
        # 条码完整规则：1→2→3→4→5→6 循环
        # =================================================================
        # 1. 先发送一次触发指令

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((camera_ip, port_trigger))
                s.sendall(trigger_cmd)
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
                s_read.connect((camera_ip, port_read))
                print(f"✅ 已连接读码端口 {port_read}，持续监听中...\n")

                while True:
                    try:
                        # 【关键修复】接收完整数据，解决分段/粘包
                        buffer = b""
                        while True:
                            chunk = s_read.recv(2048)
                            if not chunk:
                                raise ConnectionError("连接断开")
                            buffer += chunk
                            if b"}" in chunk:
                                break

                        # 解析
                        data = json.loads(buffer.decode().strip())
                        code = data.get("code", "").strip()
                        if not code or code == "NG":
                            continue

                        import time
                        from datetime import datetime

                        def format_ms(ms: float) -> str:
                            # 转秒 → 格式化 → 截取到毫秒（3位）
                            return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

                        print(f"data=> {data}, 当前读到: {code}, 时间: {format_ms( time.time_ns() / 1_000_000 )}")

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