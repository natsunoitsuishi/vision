# devices/plc_client.py
"""
PLC Modbus TCP 客户端 - 控制摆轮机方向
"""
import asyncio
import logging
from typing import Optional, Dict, Any

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from config.manager import get_config
from domain.enums import DeviceStatus, EventType
from domain.models import DeviceHealth
from services.event_bus import EventBus


class PlcDivertClient:
    """
    PLC 摆轮机控制客户端

    通过 Modbus TCP 控制摆轮机转向方向
    """

    def __init__(self, event_bus: EventBus = None):
        self.logger = logging.getLogger("plc.client")

        # 连接参数
        self.host = get_config("divert.tcp_host")
        self.port = get_config("divert.tcp_port")
        self.timeout = get_config("divert.timeout")

        # 寄存器配置
        self.direction_register = get_config("divert.registers.direction")
        self.direction_values = get_config("divert.direction_values")

        # Modbus客户端
        self._client: Optional[AsyncModbusTcpClient] = None
        self._connected = False

        # 健康状态
        self._health = DeviceHealth(
            device_id="plc_divert",
            device_type="plc",
            status=DeviceStatus.OFFLINE,
            last_heartbeat_ms=None,
            message=""
        )

        self.event_bus = event_bus

    async def connect(self) -> None:
        """建立 Modbus TCP 连接"""
        try:
            self.logger.info(f"连接 PLC ModbusTCP {self.host}:{self.port}")

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
            self.logger.info("PLC 连接成功")

        except Exception as e:
            self._connected = False
            self._update_health(DeviceStatus.OFFLINE, str(e))
            self.logger.error(f"PLC 连接失败: {e}")
            raise

    async def disconnect(self) -> None:
        """断开连接"""
        if self._client:
            self._client.close()

        self._connected = False
        self._update_health(DeviceStatus.OFFLINE, "disconnected")
        self.logger.info("PLC 已断开")

    async def set_direction(self, direction: int) -> bool:
        """
        设置摆轮机方向

        Args:
            direction: 方向编号 (1, 2, 3, 4)

        Returns:
            bool: 是否设置成功
        """
        if not self._connected or not self._client:
            self.logger.warning(f"PLC 未连接，无法设置方向 {direction}")
            return False

        # 获取写入值
        value = self.direction_values.get(direction)
        if value is None:
            self.logger.error(f"无效的方向值: {direction}")
            return False

        try:
            # 写入保持寄存器 (Holding Register)
            result = await self._client.write_register(
                address=self.direction_register,
                value=value,
                slave=1  # 设备地址，通常为1
            )

            if result.isError():
                self.logger.error(f"写入寄存器失败: {result}")
                return False

            self.logger.info(f"✅ 摆轮机方向设置成功: {direction} -> 写入值 {value}")
            self._update_health(DeviceStatus.ONLINE, f"方向 {direction} 已发送")

            # 发布事件
            if self.event_bus:
                self.event_bus.emit(
                    EventType.UI_UPDATE,
                    "plc_client",
                    {
                        "type": "divert_command",
                        "direction": direction,
                        "value": value
                    }
                )

            return True

        except ModbusException as e:
            self.logger.error(f"Modbus 写入异常: {e}")
            self._update_health(DeviceStatus.DEGRADED, f"写入失败: {e}")
            return False
        except Exception as e:
            self.logger.error(f"设置方向失败: {e}")
            return False

    async def set_direction_by_code(self, code: str) -> bool:
        """
        根据码值设置摆轮机方向

        Args:
            code: 扫码码值

        Returns:
            bool: 是否设置成功
        """
        # 将码值转换为方向 (根据您的业务规则)
        direction = self._code_to_direction(code)

        if direction is None:
            self.logger.warning(f"无法从码值 {code} 解析方向")
            return False

        return await self.set_direction(direction)

    def _code_to_direction(self, code: str) -> Optional[int]:
        """
        将码值转换为方向

        规则：code % 4 + 1
        """
        try:
            code_num = int(code) % 4
            direction = code_num + 1 if code_num != 0 else 4
            return direction
        except (ValueError, TypeError):
            return None

    def _update_health(self, status: DeviceStatus, message: str = ""):
        """更新健康状态"""
        import time
        now_ms = time.time_ns() // 1_000_000

        if self._health.status != status:
            self._health.status = status
            self._health.message = message
            self._health.last_heartbeat_ms = now_ms
            self.logger.info(f"PLC 状态 -> {status.value}: {message}")

            # 发布设备故障事件
            if self.event_bus and status != DeviceStatus.ONLINE:
                self.event_bus.emit(
                    EventType.DEVICE_FAULT,
                    "plc_client",
                    {
                        "device_id": "plc_divert",
                        "device_type": "plc",
                        "status": status.value,
                        "message": message
                    }
                )

    @property
    def is_connected(self) -> bool:
        return self._connected

    def get_health(self) -> DeviceHealth:
        return self._health