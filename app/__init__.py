"""
应用层模块

包含应用启动、生命周期管理、主入口等
"""

from .lifecycle import AppController, AppState

# 导出主要的公共接口
__all__ = [
    "AppController",
    "AppState",
]

# 可选：版本信息
__version__ = "1.0.0"