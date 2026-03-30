import logging
from typing import List, Dict, Tuple

from config import get_config
from .enums import TrackStatus
from .models import BoxTrack

logger = logging.getLogger(__name__)

def _calc_window_time(box_speed_mm_s: float, pe2_on_ms: float) -> Tuple[float, float]:
    """
    计算扫描窗口

    Args:
        box_speed_mm_s: 速度 (mm/s)
        pe2_on_ms: PE2触发时间 (毫秒)
    """
    # 获取配置（米转毫米）
    pe2_to_camera_dist_m = get_config("pe2_to_camera_dist", 0.36)
    pe2_to_camera_dist_mm = pe2_to_camera_dist_m * 1000  # 360 mm

    # 计算从 PE2 到相机的旅行时间（毫秒）
    # 时间 = 距离 / 速度
    travel_time_ms = (pe2_to_camera_dist_mm / box_speed_mm_s) * 1000
    # 窗口中心时间
    window_center_ms = pe2_on_ms + travel_time_ms

    # 窗口容差（毫秒）
    ttl_ms = get_config("trigger.ttl_ms")

    return window_center_ms - ttl_ms, window_center_ms + ttl_ms


class TriggerScheduler:
    """
        触发器调度器 - 负责计算和管理每个鞋盒的读码时间窗
    """

    def __init__(self):
        self._open_windows: Dict[str, BoxTrack] = {}

    def open_scan_window(self, track: BoxTrack, box_speed: float, pe2_on_ms: float) -> BoxTrack:
        if track.status not in [TrackStatus.CREATED, TrackStatus.TRACKING]:
            logger.warning(f"轨迹 {track.track_id} 状态异常，无法打开窗口: {track.status}")
            return track

        window_start, window_end = _calc_window_time(box_speed, pe2_on_ms)

        track.scan_window_start_ms = window_start
        track.scan_window_end_ms = window_end
        track.status = TrackStatus.WINDOW_OPEN

        # 记录打开的窗口
        self._open_windows[track.track_id] = track

        logger.info(f"打开扫描窗口: track={track.track_id}, "
                    f"start={window_start:.3f}, end={window_end:.3f}, "
                    f"duration={(window_end - window_start) * 1000:.1f}ms")
        return track


    def is_window_open(self, track: BoxTrack) -> bool:
        """检查轨迹的窗口是否开放"""
        return track.track_id in self._open_windows

    def get_open_window_count(self) -> int:
        """获取当前开放的窗口数量"""
        return len(self._open_windows)

    def get_open_windows(self) -> List[BoxTrack]:
        """获取所有开放窗口的轨迹"""
        return list(self._open_windows.values())

    def close_expired_windows(self, now_ts: float) -> List[BoxTrack]:
        """
            关闭已过期的窗口
            :param now_ts: 当前时间戳
            :return: 已关闭的轨迹列表`
        """
        expired = []
        to_remove = []

        for track_id, track in self._open_windows.items():
            # 检查窗口是否已过期
            if track.scan_window_end_ms and now_ts >= track.scan_window_end_ms:
                track.status = TrackStatus.TRACKING
                track.scan_close_reason = "TIMEOUT ..."
                expired.append(track)
                to_remove.append(track_id)
                logger.info(f"相机窗口超时关闭: {track_id}")

            # 检查是否已读到有效码
            elif track.first_ok_ms is not None:
                track.status = TrackStatus.TRACKING
                track.scan_close_reason = "OK_HOLD"
                expired.append(track)
                to_remove.append(track_id)
                logger.info(f"OK保持后关闭: {track_id}")

        # 从开放窗口字典中移除
        for track_id in to_remove:
            del self._open_windows[track_id]

        return expired