# domain/divert_scheduler.py
"""摆轮机调度器 - 控制摆轮机转向和恢复"""

import asyncio
import time
import logging
from typing import Dict, List, Optional, Tuple
from enum import Enum

from .path_config import DivertUnit, DivertStatus, PathType
from .path_planner import PathPlanner, BoxPosition


class DivertCommand(Enum):
    """摆轮机控制命令"""
    STRAIGHT = 0  # 直行
    DIVERT = 1  # 转向


class DivertScheduler:
    """
    摆轮机调度器

    职责：
    1. 监测鞋盒到达摆轮机前0.5米的位置
    2. 发送转向控制信号
    3. 鞋盒通过后恢复直行
    4. 处理连续来料的时序问题
    """

    def __init__(self, path_planner: PathPlanner, divert_units: Dict[int, DivertUnit]):
        self.path_planner = path_planner
        self.divert_units = divert_units
        self.logger = logging.getLogger(__name__)

        # 控制信号状态（模拟LED亮灯）
        self._divert_signals: Dict[int, bool] = {}  # divert_id -> is_active
        self._pending_restore: Dict[int, asyncio.Task] = {}  # 待恢复任务

        # 转向触发距离（前0.5米）
        self.trigger_distance_mm = 500  # 500mm

        # 安全距离（确保鞋盒完全通过）
        self.clearance_distance_mm = 300  # 300mm

        # 流水线暂停状态
        self._conveyor_paused: Dict[int, bool] = {}  # segment_id -> is_paused
        self._pause_start_time: Dict[int, float] = {}
        self._pause_duration: Dict[int, float] = {}

    async def update(self) -> None:
        """
        更新调度器，检查是否需要触发摆轮机
        应定期调用（如每20ms）
        """
        head_box = self.path_planner.get_head_box()
        if not head_box:
            return

        # 检查头盒是否需要触发摆轮机
        await self._check_divert_triggers(head_box)

        # 检查尾盒是否需要恢复摆轮机
        tail_box = self.path_planner.get_tail_box()
        if tail_box:
            await self._check_divert_restore(tail_box)

    async def _check_divert_triggers(self, box: BoxPosition) -> None:
        """检查是否需要触发摆轮机转向"""
        for divert_id, divert in self.divert_units.items():
            # 只处理该路径上的摆轮机
            if divert.path_type != box.path_config.path_type:
                continue

            # 检查是否已触发过
            if divert_id in box.divert_triggered:
                continue

            # 计算距离摆轮机还有多远
            distance_to_divert = divert.position_mm - box.current_pos_mm

            # 距离小于触发距离时触发转向
            if distance_to_divert <= self.trigger_distance_mm:
                self.logger.info(f"🚦 鞋盒 {box.track_id} 即将到达摆轮机 {divert_id}, "
                                 f"距离={distance_to_divert:.1f}mm, 触发转向")

                await self._trigger_divert(divert_id, box)
                self.path_planner.mark_divert_triggered(box.track_id, divert_id)

                # 记录触发时间
                divert.last_divert_time = time.time()
                divert.current_box = box.track_id

    async def _check_divert_restore(self, box: BoxPosition) -> None:
        """检查是否需要恢复摆轮机"""
        for divert_id, divert in self.divert_units.items():
            # 如果摆轮机没有活动，跳过
            if divert.status == DivertStatus.STRAIGHT:
                continue

            # 检查是否是当前摆轮机处理的鞋盒
            if divert.current_box != box.track_id:
                continue

            # 计算鞋盒当前位置距离摆轮机的距离
            distance_from_divert = box.current_pos_mm - divert.position_mm

            # 鞋盒已通过摆轮机超过安全距离，恢复直行
            if distance_from_divert >= self.clearance_distance_mm:
                self.logger.info(f"🚦 鞋盒 {box.track_id} 已通过摆轮机 {divert_id}, "
                                 f"距离={distance_from_divert:.1f}mm, 恢复直行")

                await self._restore_divert(divert_id)
                divert.status = DivertStatus.STRAIGHT
                divert.current_box = None

    async def _trigger_divert(self, divert_id: int, box: BoxPosition) -> None:
        """
        触发摆轮机转向

        模拟：点亮LED灯代表控制信号发出
        """
        self._divert_signals[divert_id] = True
        divert = self.divert_units[divert_id]
        divert.status = DivertStatus.DIVERT

        self.logger.info(f"💡 [LED] 摆轮机 {divert_id} 转向信号 ON - "
                         f"鞋盒 {box.track_id} 转向路径 {box.path_id}")

        # 实际项目中，这里应该发送硬件控制信号
        # await self._send_hardware_signal(divert_id, DivertCommand.DIVERT)

        # 延迟后关闭LED（模拟信号持续时间）
        asyncio.create_task(self._blink_led(divert_id, duration=0.5))

    async def _restore_divert(self, divert_id: int) -> None:
        """恢复摆轮机直行"""
        self._divert_signals[divert_id] = False
        divert = self.divert_units[divert_id]
        divert.status = DivertStatus.STRAIGHT

        self.logger.info(f"💡 [LED] 摆轮机 {divert_id} 转向信号 OFF - 恢复直行")

        # 实际项目中，这里应该发送硬件控制信号
        # await self._send_hardware_signal(divert_id, DivertCommand.STRAIGHT)

    async def _blink_led(self, divert_id: int, duration: float = 0.5) -> None:
        """模拟LED闪烁"""
        await asyncio.sleep(duration)
        # LED 会在恢复时关闭

    def get_divert_signal(self, divert_id: int) -> bool:
        """获取摆轮机当前信号状态"""
        return self._divert_signals.get(divert_id, False)

    # =============================
    # 流水线暂停控制
    # =============================

    def pause_conveyor(self, segment_id: int, reason: str = "") -> None:
        """
        暂停流水线

        Args:
            segment_id: 流水线段ID
            reason: 暂停原因
        """
        if self._conveyor_paused.get(segment_id, False):
            return

        self._conveyor_paused[segment_id] = True
        self._pause_start_time[segment_id] = time.time()

        self.logger.warning(f"⏸️ 流水线 {segment_id} 暂停: {reason}")

        # 实际项目中，这里应该发送暂停信号
        # await self._send_pause_signal(segment_id)

    def resume_conveyor(self, segment_id: int) -> None:
        """
        恢复流水线

        Args:
            segment_id: 流水线段ID
        """
        if not self._conveyor_paused.get(segment_id, False):
            return

        pause_duration = time.time() - self._pause_start_time.get(segment_id, 0)
        self._pause_duration[segment_id] = pause_duration

        self._conveyor_paused[segment_id] = False

        self.logger.info(f"▶️ 流水线 {segment_id} 恢复，暂停时长={pause_duration:.2f}s")

        # 实际项目中，这里应该发送恢复信号
        # await self._send_resume_signal(segment_id)

    def get_pause_duration(self, segment_id: int) -> float:
        """获取流水线暂停时长"""
        if self._conveyor_paused.get(segment_id, False):
            return time.time() - self._pause_start_time.get(segment_id, 0)
        return self._pause_duration.get(segment_id, 0)

    def is_conveyor_paused(self, segment_id: int) -> bool:
        """检查流水线是否暂停"""
        return self._conveyor_paused.get(segment_id, False)

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "active_diverts": sum(1 for d in self.divert_units.values()
                                  if d.status == DivertStatus.DIVERT),
            "signals_active": sum(1 for v in self._divert_signals.values() if v),
            "paused_segments": [k for k, v in self._conveyor_paused.items() if v]
        }