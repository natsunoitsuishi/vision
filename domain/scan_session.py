# domain/scan_session.py
"""
扫码会话控制器 - 管理相机持续扫码会话的开关
"""
import asyncio
import logging
from datetime import datetime
from typing import Dict, Optional

from devices.camera import OptCameraClient


class ScanSessionController:
    """
    扫码会话控制器 - 管理相机持续扫码会话的开关

    职责：
    1. 当活动轨迹从 0 变为 1 时启动扫码会话
    2. 当活动轨迹从 1 变为 0 时，延迟关闭扫码会话
    3. 连续来料时不会频繁启停相机

    设计原则：
    - 相机是共享资源，多个鞋盒共享同一个扫码会话
    - 只要还有活动轨迹，就保持扫码会话运行
    - 空闲后延迟关闭，避免频繁启停
    """

    def __init__(
            self,
            cameras: Dict[str, OptCameraClient],
            idle_off_delay_ms: int = 10_000,
            track_manager=None  # 可选，用于获取活动轨迹数量
    ):
        """
        初始化扫码会话控制器

        Args:
            cameras: 相机客户端字典 {camera_id: camera_client}
            idle_off_delay_ms: 空闲关闭延迟（毫秒），默认150ms
            track_manager: 轨迹管理器（可选，用于获取活动轨迹数量）
        """
        self.cameras = cameras
        self._idle_off_delay_ms = idle_off_delay_ms
        self._track_manager = track_manager

        # 会话状态
        self._is_running = False
        self._idle_stop_task: Optional[asyncio.Task] = None
        self._last_active_time = 0.0
        self._session_start_count = 0  # 会话启动次数统计
        self._session_stop_count = 0  # 会话停止次数统计

        # 日志
        self._logger = logging.getLogger(__name__)

    async def ensure_running(self) -> None:
        """
        确保扫码会话正在运行

        调用时机：
        - PE2 触发（鞋盒到达读码位置）
        - 任何需要开始扫描的时候

        行为：
        - 如果会话未运行，则启动
        - 如果会话正在运行，则刷新空闲计时器
        """
        # 刷新空闲计时器（有活动了，取消待关闭的任务）
        await self._cancel_idle_stop()

        # 如果已经在运行，只需要刷新计时器
        if self._is_running:
            self._last_active_time = datetime.now().timestamp()
            self._logger.debug(f"扫码会话已在运行，刷新空闲计时器")
            return

        # 启动扫码会话
        await self._start_session()

    async def stop_if_idle(self) -> None:
        """
        如果空闲则停止扫码会话

        调用时机：
        - 轨迹最终化后
        - 任何可能导致活动轨迹减少的时候

        行为：
        - 检查是否还有活动轨迹
        - 如果没有活动轨迹，启动延迟关闭任务
        - 如果有活动轨迹，不做任何事
        """
        # 检查是否还有活动轨迹
        has_active = self._has_active_tracks()

        if has_active:
            # 还有活动轨迹，不需要停止
            self._logger.debug(f"还有活动轨迹，不停止扫码会话")
            return

        # 没有活动轨迹，启动延迟关闭
        if not self._is_running:
            return

        # 如果已经有待关闭任务，取消重新开始
        if self._idle_stop_task and not self._idle_stop_task.done():
            self._idle_stop_task.cancel()
            self._logger.debug(f"取消之前的空闲关闭任务")

        # 创建新的延迟关闭任务
        self._idle_stop_task = asyncio.create_task(self._delayed_stop())
        self._logger.info(f"启动空闲关闭计时器: {self._idle_off_delay_ms}ms")

    async def force_stop(self) -> None:
        """
        强制停止扫码会话（立即停止，无延迟）

        调用时机：
        - 系统关闭
        - 紧急停止
        - 故障恢复
        """
        await self._cancel_idle_stop()

        if self._is_running:
            await self._stop_session()

    def is_running(self) -> bool:
        """检查扫码会话是否正在运行"""
        return self._is_running

    def get_stats(self) -> Dict:
        """获取统计信息"""
        return {
            "is_running": self._is_running,
            "session_start_count": self._session_start_count,
            "session_stop_count": self._session_stop_count,
            "idle_off_delay_ms": self._idle_off_delay_ms,
            "last_active_time": self._last_active_time
        }

    def set_idle_delay(self, delay_ms: int) -> None:
        """动态设置空闲关闭延迟"""
        self._idle_off_delay_ms = delay_ms
        self._logger.info(f"空闲关闭延迟已更新: {delay_ms}ms")

    async def _start_session(self) -> None:
        """启动扫码会话（内部方法）"""
        if self._is_running:
            return

        self._logger.info("启动扫码会话...")

        try:
            # 启动所有相机的扫码会话
            for camera_id, camera in self.cameras.items():
                if camera.is_connected():
                    await camera.start_scan_session()
                    self._logger.info(f"相机 {camera_id} 扫码会话已启动")
                else:
                    self._logger.warning(f"相机 {camera_id} 未连接，无法启动会话")

            self._is_running = True
            self._session_start_count += 1
            self._last_active_time = datetime.now().timestamp()

            self._logger.info(f"扫码会话启动成功 (总计启动次数: {self._session_start_count})")

        except Exception as e:
            self._logger.error(f"启动扫码会话失败: {e}", exc_info=True)
            # 触发报警
            await self._raise_alarm("SCAN_SESSION_START_FAILED", f"启动失败: {e}")
            raise

    async def _stop_session(self) -> None:
        """停止扫码会话（内部方法）"""
        if not self._is_running:
            return

        self._logger.info("停止扫码会话...")

        try:
            # 停止所有相机的扫码会话
            for camera_id, camera in self.cameras.items():
                if camera.is_connected():
                    await camera.stop_scan_session()
                    self._logger.info(f"相机 {camera_id} 扫码会话已停止")

            self._is_running = False
            self._session_stop_count += 1

            self._logger.info(f"扫码会话已停止 (总计停止次数: {self._session_stop_count})")

        except Exception as e:
            self._logger.error(f"停止扫码会话失败: {e}", exc_info=True)
            # 触发报警但不抛出异常
            await self._raise_alarm("SCAN_SESSION_STOP_FAILED", f"停止失败: {e}")

    async def _delayed_stop(self) -> None:
        """
        延迟停止任务

        等待空闲关闭延迟时间，如果没有新活动，则停止会话
        """
        try:
            # 等待延迟时间
            await asyncio.sleep(self._idle_off_delay_ms / 1000.0)

            # 再次检查是否还有活动轨迹（可能在等待期间又有新鞋盒）
            if self._has_active_tracks():
                self._logger.info("延迟期间检测到新活动，取消停止")
                return

            # 仍然空闲，停止会话
            if self._is_running:
                self._logger.info(f"空闲 {self._idle_off_delay_ms}ms，停止扫码会话")
                await self._stop_session()

        except asyncio.CancelledError:
            self._logger.debug("延迟停止任务被取消")
            raise
        except Exception as e:
            self._logger.error(f"延迟停止任务异常: {e}", exc_info=True)

    async def _cancel_idle_stop(self) -> None:
        """取消空闲停止任务"""
        if self._idle_stop_task and not self._idle_stop_task.done():
            self._idle_stop_task.cancel()
            try:
                await self._idle_stop_task
            except asyncio.CancelledError:
                pass
            self._idle_stop_task = None
            self._logger.debug("空闲停止任务已取消")

    def _has_active_tracks(self) -> bool:
        """
        检查是否有活动轨迹

        优先级：
        1. 如果注入了 track_manager，使用它
        2. 否则返回 False（需要外部管理）
        """
        if self._track_manager:
            return self._track_manager.has_active_tracks
        return False

    async def _raise_alarm(self, code: str, message: str) -> None:
        """触发报警（简化版）"""
        self._logger.error(f"[ALARM] {code}: {message}")
        # 实际项目中可以通过事件总线发送报警
        # if hasattr(self, 'event_bus'):
        #     self.event_bus.emit(EventType.DEVICE_FAULT, "scan_session", {
        #         "code": code,
        #         "message": message
        #     })


# 简化版（如果不依赖 track_manager）
class SimpleScanSessionController:
    """
    简化版扫码会话控制器 - 手动管理活动轨迹计数

    适用于没有 track_manager 的场景
    """

    def __init__(self, cameras: Dict[int, OptCameraClient], idle_off_delay_ms: int = 150):
        self.cameras = cameras
        self._idle_off_delay_ms = idle_off_delay_ms
        self._is_running = False
        self._active_count = 0
        self._idle_stop_task: Optional[asyncio.Task] = None
        self._logger = logging.getLogger(__name__)

    def track_created(self) -> None:
        """轨迹创建时调用"""
        self._active_count += 1
        asyncio.create_task(self.ensure_running())

    def track_finalized(self) -> None:
        """轨迹最终化时调用"""
        if self._active_count > 0:
            self._active_count -= 1

        if self._active_count == 0:
            asyncio.create_task(self.stop_if_idle())

    async def ensure_running(self) -> None:
        """确保会话运行"""
        if self._idle_stop_task:
            self._idle_stop_task.cancel()
            self._idle_stop_task = None

        if not self._is_running and self._active_count > 0:
            await self._start_session()

    async def stop_if_idle(self) -> None:
        """如果空闲则停止"""
        if self._active_count == 0 and self._is_running:
            if self._idle_stop_task:
                return

            self._idle_stop_task = asyncio.create_task(self._delayed_stop())

    async def _start_session(self) -> None:
        """启动会话"""
        for camera in self.cameras.values():
            if camera.is_connected():
                await camera.start_scan_session()
        self._is_running = True
        self._logger.info("扫码会话已启动")

    async def _stop_session(self) -> None:
        """停止会话"""
        for camera in self.cameras.values():
            if camera.is_connected():
                await camera.stop_scan_session()
        self._is_running = False
        self._logger.info("扫码会话已停止")

    async def _delayed_stop(self) -> None:
        """延迟停止"""
        try:
            await asyncio.sleep(self._idle_off_delay_ms / 1000.0)
            if self._active_count == 0 and self._is_running:
                await self._stop_session()
        except asyncio.CancelledError:
            pass
        finally:
            self._idle_stop_task = None