# enums.py
from enum import Enum, auto
from typing import List, Optional


class EventType(Enum):
    """系统事件类型枚举 - 用于事件驱动架构中的事件分类"""
    PE_RISE             = "PE_RISE"                 # 光电传感器上升沿事件（物体进入检测区域）
    PE_FALL             = "PE_FALL"                 # 光电传感器下降沿事件（物体离开检测区域）
    CAMERA_RESULT       = "CAMERA_RESULT"           # 相机识别结果事件（相机返回识别数据）
    CAMERA_HEARTBEAT    = "CAMERA_HEARTBEAT"        # 相机心跳事件（相机定期发送的心跳信号）
    TIMER_TRIGGER       = "TIMER_TRIGGER"           # 定时器触发事件（定时任务触发）
    TRACK_TIMEOUT       = "TRACK_TIMEOUT"           # 跟踪超时事件（跟踪流程超时）
    DEVICE_FAULT        = "DEVICE_FAULT"            # 设备故障事件（设备异常或离线）
    OPERATOR_CMD        = "OPERATOR_CMD"            # 操作员命令事件（人工干预指令）
    UI_UPDATE           = "UI_UPDATE"               # UI 更新事件

    @classmethod
    def list_values(cls) -> list:
        """获取所有事件类型的字符串值列表"""
        return [member.value for member in cls]

    @classmethod
    def get_device_events(cls) -> list:
        """获取所有设备相关的事件类型"""
        return [cls.PE_RISE, cls.PE_FALL, cls.CAMERA_RESULT,
                cls.CAMERA_HEARTBEAT, cls.DEVICE_FAULT]

    @classmethod
    def get_system_events(cls) -> list:
        """获取所有系统内部事件类型"""
        return [cls.TIMER_TRIGGER, cls.TRACK_TIMEOUT, cls.OPERATOR_CMD]


class RunMode(Enum):
    """运行模式枚举 - 定义系统的不同工作模式"""
    LR = "LR"  # 长距离模式（Long Range）：适用于长距离扫描场景
    FB = "FB"  # 反馈模式（Feedback）：适用于需要实时反馈的场景

    @classmethod
    def list_values(cls) -> List[str]:
        """获取所有运行模式的值列表"""
        return [member.value for member in cls]

    def is_long_range(self) -> bool:
        """判断是否为长距离模式"""
        return self == RunMode.LR

    def is_feedback(self) -> bool:
        """判断是否为反馈模式"""
        return self == RunMode.FB

class TrackStatus(Enum):
    """跟踪状态枚举 - 定义鞋盒跟踪流程的生命周期状态"""
    CREATED = "CREATED"                 # 已创建：跟踪记录刚创建，尚未开始跟踪
    TRACKING = "TRACKING"               # 跟踪中：正在跟踪鞋盒移动
    WINDOW_OPEN = "WINDOW_OPEN"         # 扫描窗口打开：允许相机扫描的时间窗口
    WAITING_RESULT = "WAITING_RESULT"   # 等待结果：已触发相机，等待识别结果

    PENDING = "pending"                 # 已创建，等待扫码
    WAITING_DIVERT = "waiting"          # 已分配路径，等待触发摆轮机
    DIVERT_TRIGGERED = "triggered"      # 已触发转向

    FINALIZED = "FINALIZED"             # 已完成：跟踪流程正常结束
    EXPIRED = "EXPIRED"                 # 已过期：跟踪超时或异常终止

    def is_active(self) -> bool:
        """判断是否为活跃状态 - 表示跟踪流程仍在进行中"""
        return self in (TrackStatus.TRACKING, TrackStatus.WINDOW_OPEN, TrackStatus.WAITING_RESULT)

    def is_terminated(self) -> bool:
        """判断是否为终止状态 - 表示跟踪流程已结束"""
        return self in (TrackStatus.FINALIZED, TrackStatus.EXPIRED)

    def can_transition_to(self, new_status: 'TrackStatus') -> bool:
        """
        检查是否可以转换到新状态
        用于状态机管理，确保状态转换的合法性

        Args:
            new_status: 目标状态

        Returns:
            bool: 是否允许转换
        """
        valid_transitions = {
            TrackStatus.CREATED: [TrackStatus.TRACKING, TrackStatus.EXPIRED],
            TrackStatus.TRACKING: [TrackStatus.WINDOW_OPEN, TrackStatus.WAITING_RESULT, TrackStatus.EXPIRED],
            TrackStatus.WINDOW_OPEN: [TrackStatus.WAITING_RESULT, TrackStatus.EXPIRED],
            TrackStatus.WAITING_RESULT: [TrackStatus.FINALIZED, TrackStatus.EXPIRED],
            TrackStatus.FINALIZED: [],  # 最终状态，不可转换
            TrackStatus.EXPIRED: []  # 最终状态，不可转换
        }
        return new_status in valid_transitions.get(self, [])

    @classmethod
    def list_active_statuses(cls) -> List['TrackStatus']:
        """获取所有活跃状态的列表"""
        return [TrackStatus.TRACKING, TrackStatus.WINDOW_OPEN, TrackStatus.WAITING_RESULT]

    @classmethod
    def list_terminated_statuses(cls) -> List['TrackStatus']:
        """获取所有终止状态的列表"""
        return [TrackStatus.FINALIZED, TrackStatus.EXPIRED]


class DecisionStatus(Enum):
    """决策状态枚举 - 定义最终识别决策结果"""
    OK          = "OK"           # 成功：成功识别条码
    NO_READ     = "NO_READ"      # 未识别：未识别到任何条码
    AMBIGUOUS   = "AMBIGUOUS"    # 歧义：识别到多个不一致的条码
    TIMEOUT     = "TIMEOUT"      # 超时：在规定时间内未完成识别
    FAULT       = "FAULT"        # 故障：系统或设备故障导致无法识别

    def is_success(self) -> bool:
        """是否为成功状态 - 表示识别成功完成"""
        return self == DecisionStatus.OK

    def is_error(self) -> bool:
        """是否为错误状态 - 表示识别失败或异常"""
        return self in (DecisionStatus.NO_READ, DecisionStatus.AMBIGUOUS,
                        DecisionStatus.TIMEOUT, DecisionStatus.FAULT)

    def is_retryable(self) -> bool:
        """
        判断是否可重试
        某些错误状态可能允许重新尝试识别
        """
        return self in (DecisionStatus.NO_READ, DecisionStatus.TIMEOUT)

    def get_severity_level(self) -> int:
        """
        获取错误严重等级
        返回数字越大表示越严重
        """
        severity_map = {
            DecisionStatus.OK: 0,
            DecisionStatus.NO_READ: 1,
            DecisionStatus.TIMEOUT: 2,
            DecisionStatus.AMBIGUOUS: 3,
            DecisionStatus.FAULT: 4
        }
        return severity_map.get(self, 5)

    @classmethod
    def list_error_statuses(cls) -> List['DecisionStatus']:
        """获取所有错误状态的列表"""
        return [cls.NO_READ, cls.AMBIGUOUS, cls.TIMEOUT, cls.FAULT]


class DeviceStatus(Enum):
    """设备状态枚举 - 定义设备的运行健康状态"""
    ONLINE      = "ONLINE"       # 在线：设备正常运行，通讯正常
    OFFLINE     = "OFFLINE"      # 离线：设备无法通讯，无心跳
    DEGRADED    = "DEGRADED"     # 降级：设备部分功能异常，但仍在运行

    def is_operational(self) -> bool:
        """设备是否可操作 - 判断设备是否能够提供基本功能"""
        return self != DeviceStatus.OFFLINE

    def is_healthy(self) -> bool:
        """设备是否健康 - 判断设备是否处于最佳状态"""
        return self == DeviceStatus.ONLINE

    def requires_attention(self) -> bool:
        """设备是否需要关注 - 判断是否需要运维人员介入"""
        return self in (DeviceStatus.OFFLINE, DeviceStatus.DEGRADED)

    @classmethod
    def list_operational_statuses(cls) -> List['DeviceStatus']:
        """获取所有可操作状态的列表"""
        return [cls.ONLINE, cls.DEGRADED]


