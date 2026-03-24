"""
配置管理模块

提供配置文件的加载、访问和管理功能
"""

from .manager import (
    ConfigManager,
    ConfigError,
    load_config,
    get_config,
    load_config_sync
)

# 导出主要的公共接口
__all__ = [
    "ConfigManager",
    "ConfigError",
    "load_config",
    "get_config",
    "load_config_sync"
]

# 可选：版本信息
__version__ = "1.0.0"