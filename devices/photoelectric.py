# devices/modbus_client.py
import asyncio
import logging
from typing import Optional, Tuple, Dict
from datetime import datetime

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from config.manager import get_config
from domain.enums import DeviceStatus, EventType
from domain.models import DeviceHealth
from services.event_bus import EventBus


class PhotoelectricClient:
    """
    ModbusTCP 客户端，用于对接设备模拟程序

    地址映射：
    - DI (离散输入): 地址0=光电1, 地址1=光电2
    - DO (线圈): 地址0=OK输出, 地址1=NG输出, 地址2=REJECT输出
    """

    def __init__(self, event_bus: EventBus):
        self.logger = logging.getLogger("modbus.client")

        # 连接参数
        self.host = get_config("modbus.host", "127.0.0.1")
        self.port = get_config("modbus.port", 15020)
        self.timeout = get_config("modbus.timeout", 3.0)

        # 脉冲时长配置（毫秒）
        self._pulse_ms = get_config("dio.pulse_ms", {
            "ok": 80,
            "ng": 120,
            "reject": 200
        })

        # DO 通道映射
        self._do_map = get_config("dio.do_map", {
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
        self._pulse_tasks: Dict[str, asyncio.Task] = {}  # 脉冲输出任务

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

        # 取消所有脉冲任务
        for task in self._pulse_tasks.values():
            if not task.done():
                task.cancel()
        self._pulse_tasks.clear()

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
    # 脉冲输出
    # =============================

    async def write_pulse(self, output_name: str) -> None:
        """
        输出脉冲（异步非阻塞）

        Args:
            output_name: 输出名称 ("ok", "ng", "reject")
        """
        if not self._connected or not self._client:
            self.logger.warning(f"Modbus 未连接，无法输出脉冲: {output_name}")
            return

        # 获取通道和脉冲时长
        channel = self._do_map.get(output_name)
        if channel is None:
            self.logger.warning(f"未知的输出名称: {output_name}")
            return

        duration_ms = self._pulse_ms.get(output_name, 100)

        self.logger.debug(f"输出脉冲: {output_name}, channel={channel}, duration={duration_ms}ms")

        # 创建脉冲任务（如果已有相同任务，先取消）
        if output_name in self._pulse_tasks and not self._pulse_tasks[output_name].done():
            self._pulse_tasks[output_name].cancel()

        # 启动新的脉冲任务
        self._pulse_tasks[output_name] = asyncio.create_task(
            self._pulse_task(channel, duration_ms / 1000.0)
        )

    async def _pulse_task(self, channel: int, duration_sec: float) -> None:
        """
        脉冲输出任务

        Args:
            channel: DO 通道号
            duration_sec: 脉冲持续时间（秒）
        """
        try:
            # 设置 DO 为 True
            await self._write_coil(channel, True)
            self.logger.debug(f"DO{channel} 已置高")

            # 等待脉冲持续时间
            await asyncio.sleep(duration_sec)

            # 设置 DO 为 False
            await self._write_coil(channel, False)
            self.logger.debug(f"DO{channel} 已置低")

        except asyncio.CancelledError:
            # 任务被取消，确保复位 DO
            try:
                await self._write_coil(channel, False)
                self.logger.debug(f"DO{channel} 脉冲被取消，已复位")
            except Exception as e:
                self.logger.error(f"脉冲取消后复位失败: {e}")
            raise
        except Exception as e:
            self.logger.error(f"脉冲输出失败: channel={channel}, error={e}")

    async def _write_coil(self, address: int, value: bool) -> None:
        """
        写入线圈（同步写入，带重试）

        Args:
            address: 线圈地址
            value: 写入值
        """
        if not self._connected or not self._client:
            raise RuntimeError("Modbus 未连接")

        try:
            result = await self._client.write_coil(address, value)

            if result.isError():
                raise ModbusException(f"写入线圈失败: {result}")

        except Exception as e:
            self.logger.error(f"写入线圈失败: address={address}, value={value}, error={e}")
            raise

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
        """
        启动DI状态监控循环

        Args:
            interval_ms: 轮询间隔（毫秒）
        """
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
        """停止监控循环"""
        self._running = False

        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        self.logger.info("停止DI监控")

    async def _monitor_loop(self, interval: float) -> None:
        """DI状态监控循环"""
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
    # 数据读写
    # =============================

    async def read_discrete_inputs(self) -> Tuple[bool, bool]:
        """
        读取离散输入（DI1和DI2）

        Returns:
            (di1, di2): 光电1和光电2的状态
        """
        if not self._connected or not self._client:
            raise RuntimeError("Modbus 未连接")

        try:
            result = await self._client.read_discrete_inputs(0, count=2)

            if result.isError():
                raise ModbusException(f"读取DI失败: {result}")

            di1 = result.bits[0]
            di2 = result.bits[1]

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

    async def write_coil(self, can_name, is_enable):
        await self._client.write_coil(0 if can_name == "cam1_enable" else 1, is_enable)

# =============================
# 使用示例
# =============================

if __name__ == '__main__':
    # from openpyxl import load_workbook
    #
    # wb = load_workbook("test.xlsx")
    # ws = wb["Sheet1"]
    #
    # print(ws)

    def calculate_missing_rate(s: str) -> dict:
        """
        计算字符串中"123456"序列的缺失率

        规则：
        - 正常序列应该是 1,2,3,4,5,6 循环
        - 遇到下降(即当前字符 <= 前一个字符)表示新序列开始
        - 统计每个完整周期应该有的字符数和实际出现的字符数
        - 缺失率 = 缺失字符数 / 应该有的字符总数

        Args:
            s: 只包含1-6字符的字符串

        Returns:
            包含缺失率、统计信息等数据的字典
        """
        if not s:
            return {"missing_rate": 0, "total_expected": 0, "total_actual": 0, "missing_count": 0}

        # 期望的完整序列
        full_sequence = ['1', '2', '3', '4', '5', '6']

        # 分割成多个序列
        sequences = []
        current_seq = [s[0]]

        for i in range(1, len(s)):
            # 如果当前字符 <= 前一个字符，说明开始了新序列
            if s[i] <= s[i - 1]:
                sequences.append(current_seq)
                current_seq = [s[i]]
            else:
                current_seq.append(s[i])
        sequences.append(current_seq)

        # 统计每个序列的缺失
        total_expected = 0  # 应该有的字符总数
        total_actual = 0  # 实际出现的字符总数

        for seq in sequences:
            # 这个序列应该有哪些字符？
            expected_chars = set()
            for ch in seq:
                # 从当前字符开始，期望一直到6
                start_idx = full_sequence.index(ch)
                for j in range(start_idx, 6):
                    expected_chars.add(full_sequence[j])

            # 实际出现的字符（去重）
            actual_chars = set(seq)

            total_expected += len(expected_chars)
            total_actual += len(actual_chars)

        missing_count = total_expected - total_actual
        missing_rate = missing_count / total_expected if total_expected > 0 else 0

        return {
            "missing_rate": missing_rate,
            "missing_rate_percent": f"{missing_rate * 100:.2f}%",
            "total_expected": total_expected,
            "total_actual": total_actual,
            "missing_count": missing_count,
            "sequences": sequences
        }


    def calculate_missing_rate_v2(s: str) -> dict:
        """
        简化版：按你的例子逻辑计算

        你的例子: "11234512346"
        1算1次, 2算1次, 3算1次, 4算1次, 5算1次, 6缺失 -> 5/6
        再1算1次, 2算1次, 3算1次, 4算1次, 5算1次, 6缺失 -> 5/6
        总应该: 12, 实际: 10, 缺失率: 2/12 = 16.67%
        """
        if not s:
            return {"missing_rate": 0}

        full = ['1', '2', '3', '4', '5', '6']
        sequences = []
        current_seq = [s[0]]

        # 按下降分割序列
        for i in range(1, len(s)):
            if s[i] <= s[i - 1]:
                sequences.append(current_seq)
                current_seq = [s[i]]
            else:
                current_seq.append(s[i])
        sequences.append(current_seq)

        total_expected = 0
        total_actual = 0

        for seq in sequences:
            # 这个序列的起始数字
            start_char = seq[0]
            start_idx = full.index(start_char)

            # 期望的字符数：从起始到6
            expected_count = 6 - start_idx
            total_expected += expected_count

            # 实际不重复的字符数
            actual_count = len(set(seq))
            total_actual += actual_count

        missing_count = total_expected - total_actual
        missing_rate = missing_count / total_expected if total_expected > 0 else 0

        return {
            "missing_rate": missing_rate,
            "missing_rate_percent": f"{missing_rate * 100:.2f}%",
            "missing_count": missing_count,
            "total_expected": total_expected,
            "total_actual": total_actual
        }


    # ========== 测试 ==========
    if __name__ == "__main__":
        # 测试用例
        test_cases = [
            ("11234512346", "你的例子"),
            ("123456123456", "完整序列"),
            ("112345612345122345", "有下降的序列"),
            ("112345123456", "第一个缺6"),
            ("111222333444555666", "重复数字"),
            ("1234512345", "一直缺6"),
        ]

        for s, desc in test_cases:
            print(f"\n{desc}: '{s}'")
            result = calculate_missing_rate_v2(s)
            print(f"  应该有的字符总数: {result['total_expected']}")
            print(f"  实际有的字符总数: {result['total_actual']}")
            print(f"  缺失数量: {result['missing_count']}")
            print(f"  缺失率: {result['missing_rate_percent']}")