# devices/modbus_client.py
import asyncio
import logging
from datetime import datetime
from typing import Optional, Tuple

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from config.manager import get_config, load_config
from domain.enums import DeviceStatus, EventType
from domain.models import DeviceHealth
from services.event_bus import EventBus

"""
    ModbusTCP 客户端，用于对接设备模拟程序
    
    地址映射：
    - DI (离散输入): 地址0=光电1, 地址1=光电2
    - DO (线圈): 地址0=OK输出, 地址1=NG输出, 地址2=REJECT输出
"""
class PhotoelectricClient:
    def __init__(self, event_bus: EventBus):
        self.logger = logging.getLogger("photoelectric.client")

        # 连接参数
        self.host = get_config("photoelectric.host", "192.168.1.117")
        self.port = get_config("photoelectric.port", 501)
        self.timeout = get_config("photoelectric.timeout", 3.0)

        # DO 通道映射
        self._do_map = get_config("photoelectric.do_map", {
            "ok": 0,
            "ng": 1,
            "reject": 2
        })

        # Modbus客户端
        self._client: Optional[AsyncModbusTcpClient] = None
        self._connected = False
        self._running = False

        # 健康状态
        self._health = DeviceHealth(
            device_id="modbus",
            device_type="photoelectric",
            status=DeviceStatus.OFFLINE,
            last_heartbeat_ts=None,
            message=""
        )

        self.event_bus = event_bus

        # 监控任务
        self._monitor_task: Optional[asyncio.Task] = None

        # 上次DI状态
        self._last_di1 = False
        self._last_di2 = False

    # =============================
    # 连接管理
    # =============================

    async def connect(self) -> None:
        """建立ModbusTCP连接"""
        try:
            self.logger.info(f"连接 ModbusTCP {self.host}:{self.port}")

            self._client = AsyncModbusTcpClient(
                host=self.host,
                port=self.port,
                timeout=self.timeout
            )

            connected = await self._client.connect()
            if not connected:
                raise ConnectionError(f"连接失败 {self.host}:{self.port}")

            self._connected = True
            self._update_health(DeviceStatus.ONLINE, "connected")
            self.logger.info(f"ModbusTCP 连接成功")

            # 连接成功后立即读取一次初始状态
            try:
                di1, di2 = await self.read_discrete_inputs()
                self._last_di1 = di1
                self._last_di2 = di2
                self.logger.info(f"初始DI状态: DI1={di1}, DI2={di2}")
            except Exception as e:
                self.logger.warning(f"读取初始状态失败: {e}")

        except Exception as e:
            self._connected = False
            self._update_health(DeviceStatus.OFFLINE, str(e))
            self.logger.error(f"ModbusTCP 连接失败: {e}")
            raise

    async def disconnect(self) -> None:
        """断开ModbusTCP连接"""
        self._running = False


        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        if self._client:
            self._client.close()

        self._connected = False
        self._update_health(DeviceStatus.OFFLINE, "disconnected")
        self.logger.info("ModbusTCP 已断开")

    # =============================
    # 监控循环
    # =============================

    async def start(self) -> None:
        """启动DI状态监控循环（兼容旧接口）"""
        await self.start_monitoring()

    async def stop(self) -> None:
        """停止监控循环（兼容旧接口）"""
        await self.stop_monitoring()

    async def start_monitoring(self, interval_ms: int = 20) -> None:
        if not self._connected:
            raise RuntimeError("Modbus 未连接，无法启动监控")

        if self._running:
            return

        self._running = True
        self._monitor_task = asyncio.create_task(
            self._monitor_loop(interval_ms / 1000.0)
        )
        self.logger.info(f"启动DI监控，间隔={interval_ms}ms")

    async def stop_monitoring(self) -> None:
        self._running = False

        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        self.logger.info("停止DI监控")

    async def _monitor_loop(self, interval: float) -> None:
        while self._running and self._connected:
            try:
                # 读取DI状态
                di1, di2 = await self.read_discrete_inputs()

                # 检查变化并发布事件
                await self._publish_di_event(di1, di2)

                # 等待下一次轮询
                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"监控循环异常: {e}")
                await asyncio.sleep(interval * 2)

    async def _publish_di_event(self, di1: bool, di2: bool) -> None:
        """发布 DI 事件"""
        timestamp = datetime.now().timestamp()

        # DI1 变化 (光电1)
        if di1 != self._last_di1:
            event_type = EventType.PE_RISE if di1 else EventType.PE_FALL
            self.logger.info(f"DI1 变化: {self._last_di1} -> {di1}")

            self.event_bus.emit(
                event_type=event_type,
                source="di1_modbus_client",
                payload={
                    "sensor": "PE1",  # 使用 sensor 字段，与 RuntimeService 期望一致
                    "channel": 1,
                    "state": di1,
                    "previous_state": self._last_di1,
                    "timestamp": timestamp
                }
            )

        # DI2 变化 (光电2)
        if di2 != self._last_di2:
            event_type = EventType.PE_RISE if di2 else EventType.PE_FALL
            self.logger.info(f"DI2 变化: {self._last_di2} -> {di2}")

            self.event_bus.emit(
                event_type=event_type,
                source="di2_modbus_client",
                payload={
                    "sensor": "PE2",  # 使用 sensor 字段，与 RuntimeService 期望一致
                    "channel": 2,
                    "state": di2,
                    "previous_state": self._last_di2,
                    "timestamp": timestamp
                }
            )

        # 更新上次状态
        self._last_di1 = di1
        self._last_di2 = di2

    # =============================
    # 数据读取
    # =============================

    async def read_discrete_inputs(self) -> Tuple[bool, bool]:
        if not self._connected or not self._client:
            raise RuntimeError("Modbus 未连接")

        try:
            photoelectric_result = await self._client.read_discrete_inputs(0, count=2)

            if photoelectric_result.isError():
                raise ModbusException(f"读取DI失败: {photoelectric_result}")

            di1 = photoelectric_result.bits[0]
            di2 = photoelectric_result.bits[1]

            return di1, di2

        except Exception as e:
            self.logger.error(f"读取DI失败: {e}")
            self._update_health(DeviceStatus.OFFLINE, f"读取失败: {e}")
            raise

    # =============================
    # 工具方法
    # =============================

    def get_health(self) -> DeviceHealth:
        """获取健康状态"""
        return self._health

    def _update_health(self, status: DeviceStatus, message: str = ""):
        """更新健康状态"""
        if self._health.status != status:
            self._health.status = status
            self._health.message = message
            self._health.last_heartbeat_ts = datetime.now().timestamp()
            self.logger.info(f"Modbus 状态 -> {status.value} {message}")

    @property
    def is_running(self) -> bool:
        """是否正在运行"""
        return self._running

    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        return self._connected

    # async def write_coil(self, can_name, is_enable):
    #     await self._client.write_coil(0 if can_name == "cam1_enable" else 1, is_enable)
    #
    # async def _write_coil(self, address: int, value: bool) -> None:
    #     if not self._connected or not self._client:
    #         raise RuntimeError("Modbus 未连接")
    #
    #     try:
    #         result = await self._client.write_coil(address, value)
    #
    #         if result.isError():
    #             raise ModbusException(f"写入线圈失败: {result}")
    #
    #     except Exception as e:
    #         self.logger.error(f"写入线圈失败: address={address}, value={value}, error={e}")
    #         raise

# =============================
# 使用示例
# =============================

if __name__ == '__main__':
    from pymodbus.client import ModbusTcpClient
    import time
    # 模块默认IP和端口
    async def main():
        await load_config()
    # 模块默认IP和端口

        IP = get_config("photoelectric.host", "192.168.1.117")

        PORT = get_config("photoelectric.port")

        # 建立连接
        client = ModbusTcpClient(IP, port=PORT)
        client.connect()

        while True:

        # 一次读取 DI1 + DI2 两个光电（地址0、地址1，共2个点）
            result = client.read_discrete_inputs(address=0, count=2)

            if not result.isError():
                # 光电1 = DI1 = 地址0
                pe1 = result.bits[0]
                # 光电2 = DI2 = 地址1
                pe2 = result.bits[1]
                print(f"光电1状态: {pe1}  |  光电2状态: {pe2}")
            else:
                print("读取失败")

            time.sleep(0.01)
        client.close()

    asyncio.run(main())
