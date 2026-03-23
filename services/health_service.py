# services/health_service.py
"""
健康检查服务 - 监控系统各组件状态
"""
import asyncio
import logging
import psutil
from typing import Dict, Any, Optional
from datetime import datetime

from domain.enums import DeviceStatus, EventType
from domain.models import DeviceHealth
from services.event_bus import EventBus


class HealthService:
    """
    健康检查服务

    职责：
    1. 定期检查相机连接状态
    2. 检查 Modbus 连接状态
    3. 检查设备心跳
    4. 监控系统资源（CPU、内存）
    5. 发现异常时发布报警事件
    """

    def __init__(
            self,
            cameras: Dict[int, Any],  # 相机客户端字典
            photoelectric_client: Any,  # Modbus 客户端
            event_bus: EventBus,
            check_interval: float = 5.0  # 检查间隔（秒）
    ):
        """
        初始化健康检查服务

        Args:
            cameras: 相机客户端字典
            photoelectric_client: Modbus 客户端
            event_bus: 事件总线
            check_interval: 检查间隔
        """
        self.cameras = cameras
        self.photoelectric_client = photoelectric_client
        self.event_bus = event_bus
        self.check_interval = check_interval
        self.logger = logging.getLogger(__name__)

        # 健康状态缓存
        self._health_status: Dict[str, DeviceHealth] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def run(self) -> None:
        """启动健康检查循环"""
        self._running = True
        self._task = asyncio.create_task(self._health_check_loop())
        self.logger.info(f"健康检查服务启动，间隔={self.check_interval}s")

    async def stop(self) -> None:
        """停止健康检查"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.logger.info("健康检查服务停止")

    async def _health_check_loop(self) -> None:
        """健康检查循环"""
        while self._running:
            try:
                await self._check_all()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"健康检查异常: {e}", exc_info=True)
                await asyncio.sleep(self.check_interval)

    async def _check_all(self) -> None:
        """检查所有组件"""
        # 1. 检查相机
        await self._check_cameras()

        # 2. 检查 Modbus
        await self._check_modbus()

        # 3. 检查系统资源
        await self._check_system_resources()

    async def _check_cameras(self) -> None:
        """检查相机状态"""
        for camera_id, camera in self.cameras.items():
            health = camera.get_health()
            old_status = self._health_status.get(f"camera_{camera_id}")

            # 状态变化时发布事件
            if old_status is None or old_status.status != health.status:
                self.logger.info(f"相机{camera_id} 状态变化: {health.status.value}")
                self.event_bus.emit(
                    EventType.CAMERA_HEARTBEAT,
                    f"health_camera_{camera_id}",
                    {
                        "camera_id": camera_id,
                        "status": health.status.value,
                        "message": health.message,
                        "timestamp": datetime.now().timestamp()
                    }
                )

                # 如果离线，发布设备故障事件
                if health.status == DeviceStatus.OFFLINE:
                    self.event_bus.emit(
                        EventType.DEVICE_FAULT,
                        "health_service",
                        {
                            "device_id": f"camera_{camera_id}",
                            "device_type": "camera",
                            "message": health.message
                        }
                    )

            self._health_status[f"camera_{camera_id}"] = health

    async def _check_modbus(self) -> None:
        """检查 Modbus 状态"""
        if not self.photoelectric_client:
            return

        health = self.photoelectric_client.get_health()
        old_status = self._health_status.get("modbus")

        if old_status is None or old_status.status != health.status:
            self.logger.info(f"Modbus 状态变化: {health.status.value}")
            self.event_bus.emit(
                EventType.DEVICE_HEARTBEAT,
                "health_modbus",
                {
                    "device_id": "modbus",
                    "device_type": "photoelectric",
                    "status": health.status.value,
                    "message": health.message
                }
            )

            if health.status == DeviceStatus.OFFLINE:
                self.event_bus.emit(
                    EventType.DEVICE_FAULT,
                    "health_service",
                    {
                        "device_id": "modbus",
                        "device_type": "photoelectric",
                        "message": health.message
                    }
                )

        self._health_status["modbus"] = health

    async def _check_system_resources(self) -> None:
        """检查系统资源"""
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()

            # CPU 过高告警
            if cpu_percent > 80:
                self.logger.warning(f"CPU 使用率过高: {cpu_percent}%")
                self.event_bus.emit(
                    EventType.DEVICE_FAULT,
                    "health_service",
                    {
                        "device_id": "system",
                        "device_type": "system",
                        "message": f"CPU 使用率过高: {cpu_percent}%"
                    }
                )

            # 内存不足告警
            if memory.percent > 85:
                self.logger.warning(f"内存使用率过高: {memory.percent}%")
                self.event_bus.emit(
                    EventType.DEVICE_FAULT,
                    "health_service",
                    {
                        "device_id": "system",
                        "device_type": "system",
                        "message": f"内存使用率过高: {memory.percent}%"
                    }
                )

        except Exception as e:
            self.logger.debug(f"获取系统资源失败: {e}")

    def get_health_summary(self) -> Dict[str, Any]:
        """获取健康状态摘要"""
        summary = {
            "cameras": {},
            "modbus": None,
            "timestamp": datetime.now().timestamp()
        }

        for name, health in self._health_status.items():
            if name.startswith("camera_"):
                summary["cameras"][name] = {
                    "status": health.status.value,
                    "message": health.message
                }
            elif name == "modbus":
                summary["modbus"] = {
                    "status": health.status.value,
                    "message": health.message
                }

        return summary