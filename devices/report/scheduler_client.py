"""
调度上位机客户端
"""
import asyncio
import logging
from typing import Optional
from datetime import datetime

import aiohttp
from aiohttp import ClientTimeout, ClientSession

from .base import BaseReportClient


class SchedulerClient(BaseReportClient):
    """调度上位机 HTTP 客户端"""

    def __init__(self, host: str = "127.0.0.1", port: int = 8080, device_id: str = "VG-01"):
        super().__init__(name="scheduler")
        self.base_url = f"http://{host}:{port}"
        self.device_id = device_id
        self._session: Optional[ClientSession] = None
        self._max_retries = 3
        self._retry_delay = 1.0

    async def connect(self) -> bool:
        try:
            self._session = ClientSession(
                timeout=ClientTimeout(total=5.0),
                headers={"Content-Type": "application/json"}
            )
            self._connected = True
            self.logger.info(f"调度上位机连接成功: {self.base_url}")
            return True
        except Exception as e:
            self.logger.error(f"调度上位机连接失败: {e}")
            self._connected = False
            return False

    async def disconnect(self) -> None:
        self._connected = False
        if self._session and not self._session.closed:
            await self._session.close()
            self.logger.info("调度上位机已断开")

    async def report_result(self, payload: dict) -> bool:
        """上报扫码结果"""
        if not self._connected or not self._session:
            self.logger.warning("调度上位机未连接，无法上报结果")
            return False

        if "device_id" not in payload:
            payload["device_id"] = self.device_id
        if "reported_at" not in payload:
            payload["reported_at"] = datetime.now().isoformat()

        return await self._post_with_retry("/api/scan/result", payload)

    async def report_heartbeat(self, payload: dict = None) -> bool:
        """上报心跳"""
        if not self._connected or not self._session:
            self.logger.warning("调度上位机未连接，无法上报心跳")
            return False

        heartbeat_payload = payload or {
            "device_id": self.device_id,
            "timestamp": datetime.now().isoformat(),
            "status": "online"
        }
        return await self._post_with_retry("/api/heartbeat", heartbeat_payload)

    async def _post_with_retry(self, endpoint: str, data: dict) -> bool:
        url = f"{self.base_url}{endpoint}"
        for attempt in range(self._max_retries):
            try:
                async with self._session.post(url, json=data) as response:
                    if response.status in (200, 201):
                        self.logger.debug(f"上报成功: {endpoint}")
                        return True
                    else:
                        self.logger.warning(f"上报失败: status={response.status}")
            except asyncio.TimeoutError:
                self.logger.warning(f"上报超时 (尝试 {attempt + 1}/{self._max_retries})")
            except Exception as e:
                self.logger.error(f"上报异常: {e}")

            if attempt < self._max_retries - 1:
                await asyncio.sleep(self._retry_delay * (2 ** attempt))
        return False