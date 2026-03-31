# domain/track_manager.py
"""
轨迹管理器 - 管理所有鞋盒轨迹的生命周期
"""
import uuid
import time
from typing import List, Optional, Dict
from datetime import datetime
import logging

from config import get_config
from domain.models import BoxTrack, CameraResult
from domain.enums import TrackStatus, DecisionStatus, RunMode


class TrackManager:
    """
    轨迹管理器

    职责：
    1. 创建鞋盒轨迹（PE1 触发时）
    2. 管理活动轨迹队列（按创建时间排序）
    3. 匹配 PE2 对应的轨迹
    4. 轨迹超时回收
    5. 轨迹状态更新和最终化

    关键规则：
    - 所有活动轨迹按创建时间升序维护
    - PE2 匹配最早进入且尚未触发的轨迹
    - 如果存在重叠歧义，立即报警
    """

    def __init__(self):
        """
        初始化轨迹管理器
        """
        self._active_tracks: List[BoxTrack] = []        # 按创建时间排序的活动轨迹
        self._finished_tracks: List[BoxTrack] = []      # 已完成的轨迹（用于归档）
        self._logger = logging.getLogger(__name__)

    def create_track(self, ts: float = None, mode: RunMode = RunMode.LR) -> BoxTrack:
        """
        创建新轨迹（PE1 上升沿触发）

        Args:
            ts: 创建时间戳（秒），默认当前时间
            mode: 运行模式

        Returns:
            新创建的轨迹对象
        """
        if ts is None:
            ts = time.time_ns() / 1_000_000

        # 生成唯一轨迹ID
        track_id = self._generate_track_id(ts)

        # 创建轨迹对象
        track = BoxTrack(
            track_id=track_id,
            mode=mode,
            created_ms=ts,
            pe1_on_ms=ts,
            status=TrackStatus.CREATED
        )

        # 添加到活动轨迹列表（保持时间顺序）
        self._active_tracks.append(track)
        self._active_tracks.sort(key=lambda t: t.created_ms)

        self._logger.info(f"[TrackManager] 创建轨迹: {track_id}, 模式={mode.value}, "
                          f"活动轨迹数={len(self._active_tracks)}")

        return track

    def match_track_for_pe2(self, ts: float) -> Optional[BoxTrack]:
        """
        匹配 PE2 对应的轨迹（PE2 上升沿触发）

        规则：
        - 匹配最早创建且尚未被 PE2 触发的轨迹
        - 如果存在重叠歧义（多个轨迹都在等待 PE2），报警

        Args:
            ts: PE2 触发时间戳

        Returns:
            匹配到的轨迹，如果没有则返回 None
        """
        # 查找第一个尚未设置 pe2_on_ms 的轨迹
        unmatched_tracks = [t for t in self._active_tracks if t.pe2_on_ms is None]

        if not unmatched_tracks:
            self._logger.error(f"[TrackManager] PE2 触发但没有等待的轨迹")
            return None

        # 如果有多个轨迹在等待 PE2，可能存在重叠歧义
        if len(unmatched_tracks) > 1:
            self._logger.warning(f"[TrackManager] PE2 触发时发现 {len(unmatched_tracks)} 个等待轨迹，存在重叠风险")
            # 标记报警（由调用方处理）
            for track in unmatched_tracks:
                if "TRACK_OVERLAP" not in track.alarm_codes:
                    track.alarm_codes.append("TRACK_OVERLAP")

        # 返回最早创建的轨迹
        track = unmatched_tracks[0]
        track.pe2_on_ms = ts
        track.status = TrackStatus.TRACKING

        self._logger.info(f"[TrackManager] PE2 匹配轨迹: {track.track_id}, "
                          f"pe2_on_ms={ts}, 剩余等待轨迹={len(unmatched_tracks) - 1}")

        return track

    def match_last_open_track(self) -> Optional[BoxTrack]:
        """
        获取最后一个打开时间窗的轨迹（用于 PE1 下降沿）

        Returns:
            最近一个打开了时间窗的轨迹
        """
        # 查找有扫描窗口且未完成的轨迹
        open_tracks = [t for t in self._active_tracks
                       if t.scan_window_start_ms is not None
                       and t.status not in [TrackStatus.FINALIZED, TrackStatus.EXPIRED]]

        if not open_tracks:
            return None

        # 返回最后一个（最近打开的）
        return open_tracks[-1]

    def get_active_tracks(self) -> List[BoxTrack]:
        """
        获取所有活动轨迹

        Returns:
            活动轨迹列表（按创建时间排序）
        """
        return self._active_tracks.copy()

    def get_track_by_id(self, track_id: str) -> Optional[BoxTrack]:
        """
        根据ID获取轨迹

        Args:
            track_id: 轨迹ID

        Returns:
            轨迹对象，如果不存在则返回 None
        """
        for track in self._active_tracks:
            if track.track_id == track_id:
                return track
        for track in self._finished_tracks:
            if track.track_id == track_id:
                return track
        return None

    def finalize_track(self, track_id: str, status: DecisionStatus) -> Optional[BoxTrack]:
        """
        最终化轨迹（输出结果后调用）

        Args:
            track_id: 轨迹ID
            status: 最终判定状态

        Returns:
            最终化的轨迹对象
        """

        track = self.get_track_by_id(track_id)
        if track is None:
            self._logger.warning(f"[TrackManager] 最终化失败，轨迹不存在: {track_id}")
            return None

        # 更新轨迹状态
        track.final_status = status
        track.status = TrackStatus.FINALIZED

        # 从活动列表移除
        if track in self._active_tracks:
            self._active_tracks.remove(track)

        # 添加到完成列表
        self._finished_tracks.append(track)

        self._logger.info(f"[TrackManager] 轨迹最终化: {track_id}, 状态={status.value}, "
                          f"剩余活动轨迹={len(self._active_tracks)}")

        return track

    def cleanup_expired(self, now_ms: float = None) -> List[BoxTrack]:
        """
        清理超时轨迹

        Args:
            now_ms: 当前时间戳，默认当前时间

        Returns:
            被清理的轨迹列表
        """
        if now_ms is None:
            now_ms = time.time_ns() / 1_000_000

        expired_tracks = []
        remaining_tracks = []

        for track in self._active_tracks:
            # 检查是否超时
            if now_ms - track.created_ms > get_config("track.ttl_ms", 1500):
                # 超时轨迹
                track.status = TrackStatus.EXPIRED
                if track.final_status is None:
                    track.final_status = DecisionStatus.TIMEOUT
                expired_tracks.append(track)
                self._finished_tracks.append(track)
                self._logger.warning(f"[TrackManager] 轨迹超时: {track.track_id}, "
                                     f"创建时间={track.created_ms}, 超时={get_config('track.ttl_ms', 150)}ms")
            else:
                remaining_tracks.append(track)

        # 更新活动轨迹列表
        self._active_tracks = remaining_tracks

        if expired_tracks:
            self._logger.info(f"[TrackManager] 清理了 {len(expired_tracks)} 个超时轨迹")

        return expired_tracks

    def update_track_speed(self, track_id: str, speed_mm_s: float) -> bool:
        """
        更新轨迹速度

        Args:
            track_id: 轨迹ID
            speed_mm_s: 速度（mm/s）

        Returns:
            是否更新成功
        """
        track = self.get_track_by_id(track_id)
        if track is None:
            return False

        track.speed_mm_s = speed_mm_s
        return True

    def update_track_length(self, track_id: str, length_mm: float) -> bool:
        """
        更新轨迹长度

        Args:
            track_id: 轨迹ID
            length_mm: 长度（mm）

        Returns:
            是否更新成功
        """
        track = self.get_track_by_id(track_id)
        if track is None:
            return False

        track.length_mm = length_mm
        return True

    def open_scan_window(self, track_id: str, window_start_ts: float, window_end_ts: float) -> bool:
        """
        打开轨迹的扫描窗口

        Args:
            track_id: 轨迹ID
            window_start_ts: 窗口开始时间
            window_end_ts: 窗口结束时间

        Returns:
            是否成功
        """
        track = self.get_track_by_id(track_id)
        if track is None:
            return False

        track.scan_window_start_ms = window_start_ts
        track.scan_window_end_ts = window_end_ts
        track.status = TrackStatus.WINDOW_OPEN

        self._logger.warning(f"[TrackManager] 打开扫描窗口: {track_id}, "
                           f"窗口={window_start_ts}~{window_end_ts}")
        return True

    def close_scan_window(self, track_id: str, reason: str = "closed") -> bool:
        """
        关闭轨迹的扫描窗口

        Args:
            track_id: 轨迹ID
            reason: 关闭原因

        Returns:
            是否成功
        """
        track = self.get_track_by_id(track_id)
        if track is None:
            return False

        track.scan_close_reason = reason
        if track.status == TrackStatus.WINDOW_OPEN:
            track.status = TrackStatus.WAITING_RESULT

        self._logger.debug(f"[TrackManager] 关闭扫描窗口: {track_id}, 原因={reason}")
        return True

    def add_camera_result(self, track_id: str, camera_result: CameraResult) -> bool:
        """
        添加相机结果到轨迹

        Args:
            track_id: 轨迹ID
            camera_result: 相机结果对象

        Returns:
            是否添加成功
        """
        track = self.get_track_by_id(track_id)
        if track is None:
            return False

        track.camera_results.append(camera_result)

        # 更新首次成功时间
        if camera_result.result == "TRUE" and track.first_ok_ms is None:
            track.first_ok_ms = camera_result.ts_ms

        return True

    def get_stats(self) -> Dict:
        """
        获取轨迹统计信息

        Returns:
            统计信息字典
        """
        return {
            "active_count": len(self._active_tracks),
            "finished_count": len(self._finished_tracks),
            "active_tracks": [
                {
                    "track_id": t.track_id,
                    "status": t.status.value,
                    "created_ms": t.created_ms,
                    "has_result": len(t.camera_results) > 0
                }
                for t in self._active_tracks
            ]
        }

    def clear_finished_tracks(self, max_keep: int = 1000):
        """
        清理已完成的轨迹（防止内存溢出）

        Args:
            max_keep: 最多保留数量
        """
        if len(self._finished_tracks) > max_keep:
            removed = self._finished_tracks[:-max_keep]
            self._finished_tracks = self._finished_tracks[-max_keep:]
            self._logger.info(f"[TrackManager] 清理了 {len(removed)} 个已完成轨迹")

    def reset(self):
        """重置所有轨迹（用于系统重启）"""
        self._active_tracks.clear()
        self._finished_tracks.clear()
        self._logger.info("[TrackManager] 所有轨迹已重置")

    @staticmethod
    def _generate_track_id(ms: float) -> str:
        """
        生成唯一轨迹ID

        格式: T{时间戳}_{随机数}
        """
        timestamp = datetime.fromtimestamp(int(ms / 1000)).strftime("%Y%m%d%H%M%S")
        short_uuid = str(uuid.uuid4())[:8]
        return f"T{timestamp}_{short_uuid}"

    @property
    def active_count(self) -> int:
        """获取活动轨迹数量"""
        return len(self._active_tracks)

    @property
    def has_active_tracks(self) -> bool:
        """是否有活动轨迹"""
        return len(self._active_tracks) > 0