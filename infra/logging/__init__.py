"""
日志模块

包含日志配置、彩色输出等
"""

from .setup import setup_logging, get_logger

__all__ = [
    "setup_logging",
    "get_logger",
]