"""
上报客户端基类
"""
from abc import ABC, abstractmethod
import logging


class BaseReportClient(ABC):
    """上报客户端抽象基类"""

    def __init__(self, name: str = "report"):
        self.name = name
        self.logger = logging.getLogger(f"{name}_client")
        self._connected = False

    @abstractmethod
    async def connect(self) -> bool:
        """建立连接"""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """断开连接"""
        pass

    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        return self._connected