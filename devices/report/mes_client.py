"""
MES 适配客户端
"""
import asyncio
import logging
from typing import Optional
from datetime import datetime

import aiohttp
from aiohttp import ClientTimeout, ClientSession

from .base import BaseReportClient

class MesClient(BaseReportClient):
    """MES 系统 HTTP 客户端"""

    def __init__(self, host: str = "127.0.0.1", port: int = 9090,
                 device_id: str = "VG-01", line_id: str = "LINE-01"):
        super().__init__(name="mes")
        self.base_url = f"http://{host}:{port}"
        self.device_id = device_id
        self.line_id = line_id
        self._session: Optional[ClientSession] = None
        self._max_retries = 3
        self._retry_delay = 1.0
        self._cache = []
        self._cache_max_size = 1000

    async def connect(self) -> bool:
        try:
            self._session = ClientSession(
                timeout=ClientTimeout(total=5.0),
                headers={"Content-Type": "application/json"}
            )
            self._connected = True
            self.logger.info(f"MES 系统连接成功: {self.base_url}")
            await self._flush_cache()
            return True
        except Exception as e:
            self.logger.error(f"MES 系统连接失败: {e}")
            self._connected = False
            return False

    async def disconnect(self) -> None:
        await self._flush_cache()
        self._connected = False
        if self._session and not self._session.closed:
            await self._session.close()
            self.logger.info("MES 系统已断开")

    async def report_scan_record(self, payload: dict) -> bool:
        """上报扫描记录"""
        mes_payload = self._build_mes_payload(payload)

        if not self._connected or not self._session:
            self._add_to_cache(mes_payload)
            return False

        success = await self._post_with_retry("/api/scan/record", mes_payload)
        if not success:
            self._add_to_cache(mes_payload)
        return success

    def _build_mes_payload(self, payload: dict) -> dict:
        """构建 MES 标准格式"""
        process_time_ms = None
        if "start_time" in payload and "end_time" in payload:
            process_time_ms = int((payload["end_time"] - payload["start_time"]) * 1000)

        return {
            "device_id": self.device_id,
            "line_id": self.line_id,
            "track_id": payload.get("track_id"),
            "mode": payload.get("mode", "LR"),
            "final_code": payload.get("final_code"),
            "status": payload.get("status", "NO_READ"),
            "process_time_ms": process_time_ms,
            "created_at": payload.get("created_at", datetime.now().isoformat())
        }

    async def _post_with_retry(self, endpoint: str, data: dict) -> bool:
        url = f"{self.base_url}{endpoint}"
        for attempt in range(self._max_retries):
            try:
                async with self._session.post(url, json=data) as response:
                    if response.status in (200, 201):
                        return True
            except (asyncio.TimeoutError, Exception):
                pass
            if attempt < self._max_retries - 1:
                await asyncio.sleep(self._retry_delay * (2 ** attempt))
        return False

    def _add_to_cache(self, payload: dict) -> None:
        self._cache.append(payload)
        if len(self._cache) > self._cache_max_size:
            self._cache.pop(0)

    async def _flush_cache(self) -> None:
        if not self._cache:
            return
        success_count = 0
        for payload in self._cache[:]:
            if await self._post_with_retry("/api/scan/record", payload):
                success_count += 1
                self._cache.remove(payload)
        self.logger.info(f"缓存上报: 成功 {success_count}, 剩余 {len(self._cache)}")

    @property
    def cache_size(self) -> int:
        return len(self._cache)