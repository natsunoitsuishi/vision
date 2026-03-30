# domain/models.py
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from .enums import RunMode, TrackStatus, DecisionStatus, DeviceStatus, EventType
import uuid
from datetime import datetime

# --- BoxTrack 模型 ---
# BoxTrack 是系统核心对象，每个经过视觉门的鞋盒都对应一个 BoxTrack

@dataclass
class CameraTriggerPlan:
    """相机触发计划 - 记录何时触发哪个相机拍照"""
    camera_id:          str                 # 相机ID，标识要触发的相机
    trigger_ts:         float               # 触发时间戳（秒），何时发送触发信号
    trigger_offset_mm:  float               # 触发偏移量（毫米），相对于参考点的物理位置
    trigger_sent:       bool = False        # 触发信号是否已发送，默认为False


@dataclass
class CameraResult:
    """
    相机扫码结果

    对应设备模拟程序返回的JSON格式：
    {
        "type": "scan_result",
        "camera_id": "CAM1",
        "result": "OK",
        "code": "QR-001",
        "symbology": "QR",
        "ts_ms": 230
    }
    """
    # 相机ID (1 或 2)
    camera_id: int

    # 扫码结果状态 (TRUE/FALSE)
    result: str

    # 码值 (result=OK时有值，NG时为NG)
    code: str

    # 码制 (UNKNOWN/QRCODE/BARCODE等)
    symbology: str

    # 仿真时间戳，单位毫秒
    ts_ms: float

    # 可选：payload 负载, 原始报文，用于调试
    raw_payload: Optional[dict] = None

    @property
    def is_success(self) -> bool:
        """判断是否扫码成功"""
        return self.result == "OK" and self.code is not None

    @property
    def status(self) -> DecisionStatus:
        """转换为决策状态"""
        if self.is_success:
            return DecisionStatus.OK
        return DecisionStatus.FAULT

    @classmethod
    def from_dict(cls, data: dict) -> "CameraResult":
        """
        从字典创建CameraResult对象

        Args:
            data: 设备模拟程序返回的JSON字典

        Returns:
            CameraResult实例
        """
        # 解析camera_id字符串为整数 ("CAM1" -> 1, "CAM2" -> 2)
        camera_id_str = data.get("camera_id", "CAM1")
        if camera_id_str.startswith("CAM"):
            camera_id = int(camera_id_str[3:])
        else:
            camera_id = int(camera_id_str)

        return cls(
            camera_id=camera_id,
            result=data.get("result", "NG"),
            code=data.get("code"),
            symbology=data.get("symbology"),
            ts_ms=data.get("ts_ms", 0),
            type=data.get("type", "scan_result")
        )

    def to_dict(self) -> dict:
        """转换为字典（用于调试或转发）"""
        return {
            "type": self.type,
            "camera_id": f"CAM{self.camera_id}",
            "result": self.result,
            "code": self.code,
            "symbology": self.symbology,
            "ts_ms": self.ts_ms
        }

    def __str__(self) -> str:
        """友好的字符串表示"""
        return (f"CameraResult(camera={self.camera_id}, "
                f"result={self.result}, code={self.code}, "
                f"symbology={self.symbology}, ts={self.ts_ms}ms)")


@dataclass
class BoxTrack:
    """核心领域模型：每个经过视觉门的鞋盒对应一个BoxTrack"""
    track_id:           str                  # 跟踪ID，唯一标识一个鞋盒跟踪记录
    mode:               RunMode              # 运行模式，如测试模式、生产模式等
    created_ms:         float                # 创建时间戳（秒），跟踪记录创建时间

    # PE 信号时间戳（光电传感器信号）
    pe1_on_ms:          Optional[float] = None  # PE1（入口光电）上升沿时间，检测到物体进入
    pe1_off_ms:         Optional[float] = None  # PE1（入口光电）下降沿时间，物体离开
    pe2_on_ms:          Optional[float] = None  # PE2（出口光电）上升沿时间，检测到物体到达出口
    pe2_off_ms:         Optional[float] = None  # PE2（出口光电）下降沿时间，物体离开出口

    # 物理参数
    speed_mm_s:     Optional[float]     = None   # 速度（毫米/秒），鞋盒通过速度
    length_mm:      Optional[float]     = None   # 长度（毫米），鞋盒物理长度

    # 扫描窗口
    scan_window_start_ms:   Optional[float] = None   # 扫描窗口开始时间，允许扫描的时间段起点
    scan_window_end_ms:     Optional[float] = None   # 扫描窗口结束时间，允许扫描的时间段终点
    first_ok_ms:            Optional[float] = None   # 首次成功识别时间戳，第一次成功读到条码的时间
    scan_close_reason:      Optional[str]   = None   # 扫描关闭原因，为何结束扫描（如超时/成功/异常）

    # 状态和结果
    status:         TrackStatus                 = TrackStatus.CREATED           # 当前跟踪状态（创建中/跟踪中/已完成等）
    trigger_plans:  List[CameraTriggerPlan]     = field(default_factory=list)   # 相机触发计划列表，预计算的触发时间点
    camera_results: List[CameraResult]          = field(default_factory=list)   # 相机识别结果列表，所有相机的返回结果
    final_code:     Optional[str]               = None                          # 最终识别码，最终确定的条码
    final_status:   Optional[DecisionStatus]    = None                          # 最终决策状态（成功/失败/超时等）
    alarm_codes:    List[str]                   = field(default_factory=list)   # 告警码列表，触发的告警信息

    def __post_init__(self):
        """初始化后的验证和计算"""
        pass

    def is_active(self) -> bool:
        """是否活跃跟踪中"""
        return self.status in [TrackStatus.TRACKING, TrackStatus.WINDOW_OPEN]

    def add_camera_result(self, result: CameraResult):
        """添加相机结果"""
        self.camera_results.append(result)
        if result.result == "OK" and not self.first_ok_ts:
            self.first_ok_ts = result.ts_ms

    def finalize(self, code: Optional[str], status: DecisionStatus):
        """完成跟踪"""
        self.final_code = code
        self.final_status = status
        self.status = TrackStatus.FINALIZED

# --- 设备快照模型 ---
@dataclass
class DeviceHealth:
    """设备健康状态 - 记录设备的实时运行状态"""
    device_id: str                      # 设备ID，唯一标识一个设备
    device_type: str                    # 设备类型，如相机/光电传感器/控制器等
    status: DeviceStatus                # 设备状态（在线/离线/故障/维护中）
    last_heartbeat_ms: float | None     # 最后心跳时间戳，最后一次收到设备心跳的时间
    message: str = ""                   # 状态消息，附加的状态说明或错误信息


# ============================================================
# 基础事件结构
# ============================================================
@dataclass
class AppEvent:
    """
    应用事件基础类
    所有系统内传递的事件都使用此结构，确保事件格式统一

    属性说明：
        event_id:   事件唯一标识符，用于追踪和去重
        event_type: 事件类型，决定如何处理该事件
        source:     事件来源，标识哪个组件/设备产生的事件
        ts:         事件时间戳（秒），事件发生的时间
        payload:    事件负载数据，包含事件相关的具体信息
    """
    event_id: str  # 事件唯一ID，格式建议：{来源}_{时间戳}_{UUID片段}
    event_type: EventType  # 事件类型，用于事件路由和处理
    source: str  # 事件源标识（如：camera_1, pe_sensor, timer_service）
    ts: float  # 事件发生时间戳（Unix时间戳，单位：秒）
    payload: Dict[str, Any] = field(default_factory=dict)  # 事件数据负载，存放具体业务数据

    def __post_init__(self):
        """初始化后的验证和处理"""
        # 确保时间戳不为负数
        if self.ts < 0:
            raise ValueError(f"无效的时间戳: {self.ts}")

    @classmethod
    def create(cls, event_type: EventType, source: str, payload: Dict[str, Any] = None) -> 'AppEvent':
        """
        工厂方法：创建新的事件实例
        自动生成事件ID和当前时间戳

        Args:
            event_type: 事件类型
            source: 事件来源
            payload: 事件数据（可选）

        Returns:
            AppEvent: 新创建的事件实例
        """
        # 生成唯一事件ID：前缀_时间戳_随机字符串
        event_id = f"{source}_{datetime.now().timestamp()}_{uuid.uuid4().hex[:8]}"

        return cls(
            event_id=event_id,
            event_type=event_type,
            source=source,
            ts=datetime.now().timestamp(),
            payload=payload or {}
        )

    def to_dict(self) -> Dict[str, Any]:
        """将事件转换为字典格式，便于序列化传输"""
        return {
            'event_id': self.event_id,
            'event_type': self.event_type.value,  # 使用枚举值
            'source': self.source,
            'ts': self.ts,
            'payload': self.payload
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AppEvent':
        """
        从字典创建事件实例，用于反序列化

        Args:
            data: 包含事件数据的字典

        Returns:
            AppEvent: 还原的事件实例
        """
        # 将字符串类型转换为枚举
        event_type = EventType(data['event_type']) if 'event_type' in data else None

        return cls(
            event_id=data.get('event_id', ''),
            event_type=event_type,
            source=data.get('source', ''),
            ts=data.get('ts', 0.0),
            payload=data.get('payload', {})
        )

    def is_device_event(self) -> bool:
        """判断是否为设备相关事件"""
        return self.event_type in EventType.get_device_events()

    def is_system_event(self) -> bool:
        """判断是否为系统内部事件"""
        return self.event_type in EventType.get_system_events()


# ============================================================
# 具体事件类型的便捷创建类（可选）
# ============================================================
class EventFactory:
    """事件工厂类：提供便捷方法创建特定类型的事件"""

    @staticmethod
    def create_pe_rise(sensor_id: str, timestamp: float = None) -> AppEvent:
        """创建PE上升沿事件"""
        return AppEvent.create(
            event_type=EventType.PE_RISE,
            source=sensor_id,
            payload={
                'sensor_id': sensor_id,
                'edge': 'rise',
                'timestamp': timestamp or datetime.now().timestamp()
            }
        )

    @staticmethod
    def create_camera_result(camera_id: str, result_data: Dict[str, Any]) -> AppEvent:
        """创建相机结果事件"""
        return AppEvent.create(
            event_type=EventType.CAMERA_RESULT,
            source=camera_id,
            payload={
                'camera_id': camera_id,
                'result': result_data,
                'code': result_data.get('code'),
                'success': result_data.get('success', False)
            }
        )

    @staticmethod
    def create_track_timeout(track_id: str, timeout_duration: float) -> AppEvent:
        """创建跟踪超时事件"""
        return AppEvent.create(
            event_type=EventType.TRACK_TIMEOUT,
            source='track_manager',
            payload={
                'track_id': track_id,
                'timeout_duration': timeout_duration,
                'timeout_at': datetime.now().timestamp()
            }
        )

    @staticmethod
    def create_device_fault(device_id: str, fault_reason: str) -> AppEvent:
        """生建设备故障事件"""
        return AppEvent.create(
            event_type=EventType.DEVICE_FAULT,
            source=device_id,
            payload={
                'device_id': device_id,
                'reason': fault_reason,
                'fault_time': datetime.now().timestamp()
            }
        )