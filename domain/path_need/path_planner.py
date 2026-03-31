# domain/path_planner.py
"""路径规划器 - 计算鞋盒在传送带上的位置和到达各摆轮机的时间"""

import time
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from collections import deque

from .path_config import PathConfig, DivertUnit, PathType, DEFAULT_PATHS, DEFAULT_DIVERT_UNITS
from .. import BoxTrack


@dataclass
class BoxPosition:
    """鞋盒实时位置"""
    track_id: str
    current_pos_mm: float  # 当前位置（距离扫码点）
    speed_mm_s: float  # 当前速度
    path_id: int  # 目标路径
    path_config: PathConfig  # 路径配置
    last_update_ms: float  # 最后更新时间
    has_entered_path: bool = False  # 是否已进入分支路径
    divert_triggered: List[int] = field(default_factory=list)  # 已触发的摆轮机

    # 到达各摆轮机的时间预估
    estimated_arrivals: Dict[int, float] = field(default_factory=dict)


class PathPlanner:
    """
    路径规划器

    职责：
    1. 根据扫码结果规划鞋盒的目标路径
    2. 实时计算鞋盒当前位置
    3. 预估到达各摆轮机的时间
    4. 检测头盒和尾盒位置
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)

        # 路径配置
        self.paths = DEFAULT_PATHS
        self.divert_units = DEFAULT_DIVERT_UNITS

        # 活动鞋盒（按位置排序）
        self.active_boxes: Dict[str, BoxPosition] = {}
        self._box_queue = deque()  # 按位置排序的队列

        # 头盒和尾盒追踪
        self.head_box: Optional[BoxPosition] = None
        self.tail_box: Optional[BoxPosition] = None

        # 统计
        self._stats = {
            "total_boxes": 0,
            "completed_boxes": 0,
            "rejected_boxes": 0
        }

    def assign_path(self, track: BoxTrack, code: str) -> Optional[PathConfig]:
        """
        根据扫码结果为鞋盒分配路径

        Args:
            track: 鞋盒轨迹
            code: 扫码码值

        Returns:
            分配的路径配置，失败返回None
        """
        # 根据码值映射到路径（可根据实际业务规则修改）
        path_id = self._code_to_path(code)

        if path_id is None:
            self.logger.warning(f"码值 {code} 无法映射到有效路径，将送往异常处理线")
            return None

        path = self.paths.get(path_id)
        if not path:
            self.logger.error(f"路径 {path_id} 不存在")
            return None

        self.logger.info(f"鞋盒 {track.track_id} 分配路径 {path_id}: {path.destination}")

        return path

    def _code_to_path(self, code: str) -> Optional[int]:
        """
        将扫码码值映射到路径ID
        可根据实际业务规则自定义
        """
        try:
            # 示例：码值 1-4 对应路径 1-4
            code_num = int(code)
            if 1 <= code_num <= 4:
                return code_num
        except ValueError:
            pass

        # 其他规则...
        return None

    def add_box(self, track: BoxTrack, path: Optional[PathConfig],
                speed_mm_s: float = 500.0) -> Optional[BoxPosition]:
        """
        添加鞋盒到路径规划器

        Args:
            track: 鞋盒轨迹
            path: 分配的路径
            speed_mm_s: 速度

        Returns:
            鞋盒位置对象
        """
        if path is None:
            # 失败盒，走异常处理线
            path = PathConfig(
                path_id=0,
                path_type=PathType.REJECT,
                length_mm=5000,
                divert_units=[],
                destination="异常处理线"
            )

        now_ms = time.time_ns() / 1_000_000

        box = BoxPosition(
            track_id=track.track_id,
            current_pos_mm=0.0,  # 从扫码点开始
            speed_mm_s=speed_mm_s,
            path_id=path.path_id,
            path_config=path,
            last_update_ms=now_ms
        )

        # 计算到达各摆轮机的时间
        self._calculate_arrivals(box)

        self.active_boxes[track.track_id] = box
        self._update_queue()
        self._update_head_tail()

        self._stats["total_boxes"] += 1

        self.logger.info(f"添加鞋盒 {track.track_id} 到路径 {path.path_id}, "
                         f"速度={speed_mm_s}mm/s")

        return box

    def update_positions(self, elapsed_ms: float) -> None:
        """
        更新所有鞋盒位置

        Args:
            elapsed_ms: 经过的时间（毫秒）
        """
        elapsed_s = elapsed_ms / 1000.0

        for box in list(self.active_boxes.values()):
            # 更新位置
            box.current_pos_mm += box.speed_mm_s * elapsed_s
            box.last_update_ms += elapsed_ms

            # 检查是否已走完路径
            if box.current_pos_mm >= box.path_config.length_mm:
                self._complete_box(box)
                continue

            # 更新到达时间预估
            self._update_arrivals(box)

        self._update_queue()
        self._update_head_tail()

    def _calculate_arrivals(self, box: BoxPosition) -> None:
        """计算到达各摆轮机的时间"""
        for divert_id, divert in self.divert_units.items():
            if divert.path_type != box.path_config.path_type:
                continue

            # 摆轮机在路径上的位置
            divert_pos_mm = divert.position_mm

            if divert_pos_mm <= box.current_pos_mm:
                # 已经过该摆轮机
                continue

            # 计算到达时间（毫秒）
            distance_mm = divert_pos_mm - box.current_pos_mm
            time_ms = (distance_mm / box.speed_mm_s) * 1000
            box.estimated_arrivals[divert_id] = time_ms

    def _update_arrivals(self, box: BoxPosition) -> None:
        """更新到达时间预估"""
        for divert_id in list(box.estimated_arrivals.keys()):
            remaining = box.estimated_arrivals[divert_id] - (time.time_ns() / 1_000_000 - box.last_update_ms)
            if remaining <= 0:
                # 已到达
                del box.estimated_arrivals[divert_id]
            else:
                box.estimated_arrivals[divert_id] = remaining

    def get_head_box(self) -> Optional[BoxPosition]:
        """获取头盒（最前面的鞋盒）"""
        return self.head_box

    def get_tail_box(self) -> Optional[BoxPosition]:
        """获取尾盒（最后面的鞋盒）"""
        return self.tail_box

    def get_boxes_before_position(self, position_mm: float) -> List[BoxPosition]:
        """获取位置在指定点之前的鞋盒"""
        return [b for b in self.active_boxes.values()
                if b.current_pos_mm < position_mm]

    def get_boxes_after_position(self, position_mm: float) -> List[BoxPosition]:
        """获取位置在指定点之后的鞋盒"""
        return [b for b in self.active_boxes.values()
                if b.current_pos_mm > position_mm]

    def _update_queue(self):
        """更新队列排序"""
        self._box_queue = deque(sorted(
            self.active_boxes.values(),
            key=lambda b: b.current_pos_mm
        ))

    def _update_head_tail(self):
        """更新头盒和尾盒"""
        if self._box_queue:
            self.head_box = self._box_queue[-1]  # 位置最大的
            self.tail_box = self._box_queue[0]  # 位置最小的
        else:
            self.head_box = None
            self.tail_box = None

    def _complete_box(self, box: BoxPosition):
        """完成鞋盒路径"""
        self.logger.info(f"鞋盒 {box.track_id} 已完成路径 {box.path_id}, "
                         f"总行程={box.current_pos_mm:.1f}mm")

        del self.active_boxes[box.track_id]
        self._stats["completed_boxes"] += 1

        self._update_queue()
        self._update_head_tail()

    def mark_divert_triggered(self, track_id: str, divert_id: int) -> None:
        """标记摆轮机已触发"""
        box = self.active_boxes.get(track_id)
        if box:
            if divert_id not in box.divert_triggered:
                box.divert_triggered.append(divert_id)

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            **self._stats,
            "active_count": len(self.active_boxes),
            "head_box": self.head_box.track_id if self.head_box else None,
            "tail_box": self.tail_box.track_id if self.tail_box else None
        }