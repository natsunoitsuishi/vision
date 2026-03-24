# domain/scheduler.py
import logging
from dataclasses import dataclass
from typing import List, Optional, Dict

from .enums import RunMode, TrackStatus
from .models import BoxTrack

logger = logging.getLogger(__name__)


@dataclass
class SchedulerConfig:
    """调度器配置参数"""
    # 时间窗参数（毫秒）
    tail_delay_ms: int = 500  # PE1下降沿后的延迟（尾部通过后还能读多久）
    max_track_window_ms: int = 5000  # 最大跟踪窗口（从创建开始算）
    track_hold_after_ok_ms: int = 200  # 读到OK码后的保持时间（毫秒）

    # 速度参数
    default_line_speed_mm_s: float = 500.0  # 默认线速度（毫米/秒）
    speed_min_mm_s: float = 100.0  # 最小有效速度
    speed_max_mm_s: float = 2000.0  # 最大有效速度

    # 读码区域参数
    read_zone_length_mm: float = 300.0  # 可读码区域长度（毫米）
    safe_margin_ms: int = 100  # 安全余量（毫秒）


class TriggerScheduler:
    """
    触发器调度器 - 负责计算和管理每个鞋盒的读码时间窗
    根据BoxTrack的PE信号、速度和配置计算精确扫描窗口
    """

    def __init__(self, config: SchedulerConfig = None):
        self.config = config or SchedulerConfig()
        # 跟踪当前打开的窗口（track_id -> BoxTrack）
        self._open_windows: Dict[str, BoxTrack] = {}

    def open_scan_window(self, track: BoxTrack, mode: RunMode) -> BoxTrack:
        """
        打开扫描窗口
        :param track: 目标轨迹
        :param mode: 运行模式
        :return: 更新后的轨迹
        """
        if track.status not in [TrackStatus.CREATED, TrackStatus.TRACKING]:
            logger.warning(f"轨迹 {track.track_id} 状态异常，无法打开窗口: {track.status}")
            return track

        # 计算窗口开始时间（通常取PE2触发时间）
        window_start = self._calc_window_start(track)
        if window_start is None:
            # 无法计算开始时间，使用创建时间
            window_start = track.created_ts
            track.alarm_codes.append("WINDOW_START_FAILED ...")

        # 计算窗口结束时间
        window_end = self._calc_window_end(track)
        if window_end is None:
            # 无法计算结束时间，使用默认最大窗口
            window_end = track.created_ts + self.config.max_track_window_ms / 1000.0
            track.alarm_codes.append("WINDOW_END_FAILED ...")

        # 更新轨迹
        track.scan_window_start_ts = window_start
        track.scan_window_end_ts = window_end
        track.status = TrackStatus.WINDOW_OPEN

        # 记录打开的窗口
        self._open_windows[track.track_id] = track

        logger.info(f"打开扫描窗口: track={track.track_id}, "
                    f"start={window_start:.3f}, end={window_end:.3f}, "
                    f"duration={(window_end - window_start) * 1000:.1f}ms")
        return track

    def prepare_window_close(self, track: BoxTrack, pe1_fall_ts: float) -> BoxTrack:
        """
        准备关闭窗口（当PE1下降沿触发时）
        :param track: 目标轨迹
        :param pe1_fall_ts: PE1下降沿时间戳
        :return: 更新后的轨迹
        """
        if track.status != TrackStatus.WINDOW_OPEN:
            return track

        # 记录PE1下降沿时间
        track.pe1_off_ts = pe1_fall_ts

        # 重新计算窗口结束时间（基于实际PE1下降沿）
        new_end = self._calc_window_end_with_pe1_fall(track)

        # 如果新结束时间更早，则更新
        if new_end and (track.scan_window_end_ts is None or new_end < track.scan_window_end_ts):
            old_end = track.scan_window_end_ts
            track.scan_window_end_ts = new_end
            logger.info(f"更新窗口结束时间: track={track.track_id}, "
                        f"old={old_end:.3f}, new={new_end:.3f}")

        return track

    def close_expired_windows(self, now_ts: float) -> List[BoxTrack]:
        """
        关闭已过期的窗口
        :param now_ts: 当前时间戳
        :return: 已关闭的轨迹列表
        """
        expired = []
        to_remove = []

        for track_id, track in self._open_windows.items():
            # 检查窗口是否已过期
            if track.scan_window_end_ts and now_ts >= track.scan_window_end_ts:
                track.status = TrackStatus.TRACKING  # 回到跟踪状态
                track.scan_close_reason = "TIMEOUT ..."
                expired.append(track)
                to_remove.append(track_id)
                logger.debug(f"窗口超时关闭: {track_id}")

            # 检查是否已读到有效码且过了保持时间
            elif (track.first_ok_ts is not None and
                  track.scan_window_end_ts and
                  now_ts - track.first_ok_ts >= self.config.track_hold_after_ok_ms / 1000.0):

                # 如果OK保持时间结束，可以提前关闭窗口
                if now_ts < track.scan_window_end_ts:
                    # 更新窗口结束时间为当前时间+安全余量
                    track.scan_window_end_ts = now_ts + self.config.safe_margin_ms / 1000.0

                track.status = TrackStatus.TRACKING
                track.scan_close_reason = "OK_HOLD"
                expired.append(track)
                to_remove.append(track_id)
                logger.debug(f"OK保持后关闭: {track_id}")

        # 从开放窗口字典中移除
        for track_id in to_remove:
            del self._open_windows[track_id]

        return expired

    def _calc_window_start(self, track: BoxTrack) -> Optional[float]:
        """计算窗口开始时间"""
        # 优先使用PE2触发时间（物体到达出口光电）
        if track.pe2_on_ts is not None:
            return track.pe2_on_ts

        # 如果没有PE2，使用创建时间（通常是PE1触发）
        if track.created_ts is not None:
            return track.created_ts

        return None

    def _calc_window_end(self, track: BoxTrack) -> Optional[float]:
        """计算窗口结束时间"""
        # 获取有效速度
        speed = self._get_effective_speed(track)

        # 基础结束时间：从创建时间开始的最大窗口
        max_end = track.created_ts + self.config.max_track_window_ms / 1000.0

        # 如果有PE2时间，基于PE2和速度预测离开时间
        if track.pe2_on_ts is not None:
            # 计算通过读码区域所需时间
            travel_time = self.config.read_zone_length_mm / speed
            predicted_end = track.pe2_on_ts + travel_time

            # 取预测时间和最大窗口的较小值
            window_end = min(predicted_end, max_end)
        else:
            # 没有PE2，直接用最大窗口
            window_end = max_end

        return window_end

    def _calc_window_end_with_pe1_fall(self, track: BoxTrack) -> Optional[float]:
        """基于PE1下降沿计算窗口结束时间"""
        if track.pe1_off_ts is None:
            return track.scan_window_end_ts

        # 尾部延迟：物体尾部通过后还能读取的时间
        tail_delay = self.config.tail_delay_ms / 1000.0
        predicted_end = track.pe1_off_ts + tail_delay

        # 不超过最大窗口
        max_end = track.created_ts + self.config.max_track_window_ms / 1000.0

        return min(predicted_end, max_end)

    def _get_effective_speed(self, track: BoxTrack) -> float:
        """获取有效速度（无效则返回默认值）"""
        # 如果轨迹有速度且有效，使用它
        if (track.speed_mm_s is not None and
                self.config.speed_min_mm_s <= track.speed_mm_s <= self.config.speed_max_mm_s):
            return track.speed_mm_s

        # 否则使用默认速度
        return self.config.default_line_speed_mm_s

    def is_window_open(self, track: BoxTrack) -> bool:
        """检查轨迹的窗口是否开放"""
        return track.track_id in self._open_windows

    def get_open_window_count(self) -> int:
        """获取当前开放的窗口数量"""
        return len(self._open_windows)

    def get_open_windows(self) -> List[BoxTrack]:
        """获取所有开放窗口的轨迹"""
        return list(self._open_windows.values())