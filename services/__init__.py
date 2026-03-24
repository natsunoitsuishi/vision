"""
服务层模块

包含运行时服务、事件总线、健康检查等核心服务
"""

from .event_bus import EventBus, create_event_bus, event_listener
from .runtime_service import RuntimeService
from .health_service import HealthService

# 可选：如果存在其他服务
# from .archive_service import ArchiveService
# from .config_service import ConfigService

__all__ = [
    # 事件总线
    "EventBus",
    "create_event_bus",
    "event_listener",

    # 运行时服务
    "RuntimeService",

    # 健康检查
    "HealthService",

    # 其他服务（如果存在）
    # "ArchiveService",
    # "ConfigService",
]

__version__ = "1.0.0"