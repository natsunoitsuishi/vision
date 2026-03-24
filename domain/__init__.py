"""
领域模型模块

包含核心业务模型、枚举、轨迹管理、调度、绑定、决策等
"""

# 模型
from .models import (
    BoxTrack,
    CameraResult,
    CameraTriggerPlan,
    DeviceHealth,
    AppEvent
)

# 枚举
from .enums import (
    RunMode,
    TrackStatus,
    DecisionStatus,
    DeviceStatus,
    EventType
)

# 核心服务
from .track_manager import TrackManager
from .scheduler import TriggerScheduler, SchedulerConfig
# from .scan_session import ScanSessionController, SimpleScanSessionController
from .binder import ResultBinder
from .decision_engine import DecisionEngine

# 事件工厂
from .models import EventFactory

__all__ = [
    # 模型
    "BoxTrack",
    "CameraResult",
    "CameraTriggerPlan",
    "DeviceHealth",
    "AppEvent",

    # 枚举
    "RunMode",
    "TrackStatus",
    "DecisionStatus",
    "DeviceStatus",
    "EventType",

    # 核心服务
    "TrackManager",
    "TriggerScheduler",
    "SchedulerConfig",
    "ScanSessionController",
    "SimpleScanSessionController",
    "ResultBinder",
    "DecisionEngine",

    # 事件工厂
    "EventFactory",
]

# 版本信息
__version__ = "1.0.0"