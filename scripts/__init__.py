"""
脚本工具模块

包含项目路径、配置路径等工具函数
"""

from .util import (
    get_project_root,
    get_project_config_path,
)

__all__ = [
    "get_project_root",
    "get_project_config_path",
]

# 版本信息
__version__ = "1.0.0"