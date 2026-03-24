# domain/binder.py
"""
结果绑定器 - 将相机结果绑定到正确的鞋盒轨迹
"""
import logging
from typing import List, Optional, Tuple

from .enums import DecisionStatus
from .models import BoxTrack, CameraResult

class ResultBinder:
    """
    结果绑定器 - 负责将相机结果绑定到正确的轨迹

    绑定规则：
    1. 优先按 camera_id + ts_ms 命中活动时间窗
    2. 若只命中一个轨迹，直接绑定
    3. 若命中多个轨迹，优先选择距离窗口中心最近者
    4. 若仍不能唯一确定，则判为歧义
    5. 未命中任何活动轨迹时，记录 UNBOUND_RESULT
    """

    def __init__(self, config: dict = None):
        """
        初始化结果绑定器

        Args:
            config: 配置字典，包含时间窗容差等参数
        """
        self.config = config or {}
        self._window_tolerance_ms = self.config.get("window_tolerance_ms", 50)  # 窗口边界容差（毫秒）
        self._logger = logging.getLogger(__name__)

        # 统计信息
        self._stats = {
            "total_bound": 0,
            "unbound": 0,
            "ambiguous": 0,
            "single_hit": 0,
            "multi_hit": 0
        }

    def bind(self, result: CameraResult, active_tracks: List[BoxTrack]) -> Optional[BoxTrack]:
        """
        将相机结果绑定到最匹配的轨迹

        Args:
            result: 相机结果
            active_tracks: 活动轨迹列表

        Returns:
            匹配到的轨迹，如果没有则返回 None
        """
        if not active_tracks:
            self._logger.warning(f"相机结果无法绑定：没有活动轨迹, ts_ms={result.ts_ms}")
            self._stats["unbound"] += 1
            return None

        # 查找所有可能匹配的轨迹
        candidates = self._find_candidate_tracks(result, active_tracks)
        print(f"candidates: {candidates}")

        if not candidates:
            self._logger.debug(f"相机结果未命中任何时间窗: camera={result.camera_id}, "
                               f"ts={result.ts_ms:.3f}")
            self._stats["unbound"] += 1
            return None

        # 根据规则选择最佳匹配
        best_track = self._select_best_match(result, candidates)

        if best_track is None:
            self._logger.warning(f"相机结果匹配歧义: camera={result.camera_id}, "
                                 f"ts={result.ts_ms:.3f}, 候选数={len(candidates)}")
            self._stats["ambiguous"] += 1
            return None

        # 记录匹配结果
        self._stats["total_bound"] += 1
        if len(candidates) == 1:
            self._stats["single_hit"] += 1
        else:
            self._stats["multi_hit"] += 1

        self._logger.debug(f"结果绑定成功: track={best_track.track_id}, "
                           f"camera={result.camera_id}, ts={result.ts_ms:.3f}")

        return best_track

    def resolve_final_code(self, track: BoxTrack) -> Tuple[Optional[str], Optional[DecisionStatus]]:
        """
        解析轨迹的最终码值和状态

        Args:
            track: 轨迹对象

        Returns:
            (final_code, final_status) 如果已满足判定条件则返回，否则返回 (None, None)
        """
        if not track.camera_results:
            return None, None

        # 检查是否已经读到有效码
        successful_results = [r for r in track.camera_results if r.result == "OK"]

        if not successful_results:
            return None, None

        # 收集所有成功的结果
        codes = [r.code for r in successful_results if r.code]
        unique_codes = list(set(codes))

        # 判断是否有足够的信息做出决策
        # 这里返回码值和状态，由 DecisionEngine 做最终判定
        if unique_codes:
            # 有成功的结果，返回第一个码值（后续由 DecisionEngine 处理冲突）
            return unique_codes[0], None

        return None, None

    def _find_candidate_tracks(self, result: CameraResult, active_tracks: List[BoxTrack]) -> List[BoxTrack]:
        """
        查找时间窗包含该结果时间戳的轨迹

        Args:
            result: 相机结果
            active_tracks: 活动轨迹列表

        Returns:
            候选轨迹列表
        """
        candidates = []

        for track in active_tracks:
            # 检查轨迹是否有有效的时间窗
            if track.scan_window_start_ts is None or track.scan_window_end_ts is None:
                continue

            # 检查时间戳是否在窗口内（带容差）
            if self._is_in_window(result.ts_ms, track.scan_window_start_ts, track.scan_window_end_ts):
                candidates.append(track)

        return candidates

    def _is_in_window(self, ts: float, start: float, end: float) -> bool:
        """
        检查时间戳是否在窗口内（带容差）

        Args:
            ts: 时间戳
            start: 窗口开始时间
            end: 窗口结束时间

        Returns:
            是否在窗口内
        """
        tolerance = self._window_tolerance_ms / 1000.0  # 转换为秒
        return start - tolerance <= ts <= end + tolerance

    def _select_best_match(self, result: CameraResult, candidates: List[BoxTrack]) -> Optional[BoxTrack]:
        """
        从候选轨迹中选择最佳匹配

        规则：
        1. 如果只有一个候选，直接返回
        2. 如果有多个候选，选择距离窗口中心最近的
        3. 如果距离相同，选择最早创建的

        Args:
            result: 相机结果
            candidates: 候选轨迹列表

        Returns:
            最佳匹配轨迹
        """
        if not candidates:
            return None

        if len(candidates) == 1:
            return candidates[0]

        # 多个候选，计算每个候选的距离窗口中心的距离
        best_track = None
        best_distance = float('inf')

        for track in candidates:
            if track.scan_window_start_ts is None or track.scan_window_end_ts is None:
                continue

            # 计算窗口中心
            window_center = (track.scan_window_start_ts + track.scan_window_end_ts) / 2.0

            # 计算距离
            distance = abs(result.ts_ms - window_center)

            if distance < best_distance - 0.001:  # 有更小的距离
                best_distance = distance
                best_track = track
            elif abs(distance - best_distance) < 0.001:  # 距离相等
                # 选择更早创建的
                if best_track and track.created_ts < best_track.created_ts:
                    best_track = track

        return best_track

    def get_stats(self) -> dict:
        """获取绑定统计信息"""
        return {
            **self._stats,
            "hit_rate": self._stats["total_bound"] / (self._stats["total_bound"] + self._stats["unbound"])
            if (self._stats["total_bound"] + self._stats["unbound"]) > 0 else 0
        }

    def reset_stats(self) -> None:
        """重置统计信息"""
        self._stats = {
            "total_bound": 0,
            "unbound": 0,
            "ambiguous": 0,
            "single_hit": 0,
            "multi_hit": 0
        }

