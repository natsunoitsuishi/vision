# services/archive_service.py
import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from enum import Enum
import math


class PositionSource(Enum):
    """位置更新来源"""
    PE_RISE = "PE_RISE"  # 光电上升沿
    PE_FALL = "PE_FALL"  # 光电下降沿
    CAMERA_RESULT = "CAMERA_RESULT"  # 相机识别结果
    TIME_UPDATE = "TIME_UPDATE"  # 时间推移自动更新


@dataclass
class BoxPosition:
    """鞋盒位置信息"""
    track_id: str  # 轨迹ID
    created_at: float  # 创建时间
    last_update: float  # 最后更新时间

    # 位置信息（单位：毫米）
    current_position_mm: float = 0.0  # 当前距离入口光电的距离
    distance_to_camera1_mm: float = 0.0  # 距离相机1的距离
    distance_to_camera2_mm: float = 0.0  # 距离相机2的距离
    distance_to_exit_mm: float = 0.0  # 距离出口的距离

    # 物理参数
    speed_mm_s: float = 0.0  # 当前速度（mm/s）
    length_mm: float = 0.0  # 鞋盒长度（mm）

    # 事件时间戳
    pe1_rise_ts: Optional[float] = None  # PE1上升沿时间
    pe1_fall_ts: Optional[float] = None  # PE1下降沿时间
    pe2_rise_ts: Optional[float] = None  # PE2上升沿时间
    pe2_fall_ts: Optional[float] = None  # PE2下降沿时间

    # 识别结果
    camera1_result: Optional[str] = None  # 相机1识别结果
    camera2_result: Optional[str] = None  # 相机2识别结果
    final_code: Optional[str] = None  # 最终码值
    final_status: Optional[str] = None  # 最终状态

    # 状态
    is_active: bool = True  # 是否在传送带上
    has_entered: bool = False  # 是否已进入
    has_exited: bool = False  # 是否已离开

    # 位置更新历史（用于调试）
    position_history: List[Tuple[float, float, PositionSource]] = field(default_factory=list)

    def update_position(self, new_position_mm: float, source: PositionSource, timestamp: float = None):
        """更新位置"""
        if timestamp is None:
            timestamp = time.time()

        self.current_position_mm = new_position_mm
        self.last_update = timestamp

        # 记录历史（最多保留50条）
        self.position_history.append((timestamp, new_position_mm, source))
        if len(self.position_history) > 50:
            self.position_history.pop(0)

    def to_dict(self) -> dict:
        """转换为字典，供MES上报"""
        return {
            "track_id": self.track_id,
            "current_position_mm": round(self.current_position_mm, 1),
            "distance_to_camera1_mm": round(self.distance_to_camera1_mm, 1),
            "distance_to_camera2_mm": round(self.distance_to_camera2_mm, 1),
            "distance_to_exit_mm": round(self.distance_to_exit_mm, 1),
            "speed_mm_s": round(self.speed_mm_s, 1),
            "length_mm": round(self.length_mm, 1),
            "is_active": self.is_active,
            "has_entered": self.has_entered,
            "has_exited": self.has_exited,
            "camera1_result": self.camera1_result,
            "camera2_result": self.camera2_result,
            "final_code": self.final_code,
            "final_status": self.final_status,
            "created_at": self.created_at,
            "last_update": self.last_update,
            "pe1_rise_ts": self.pe1_rise_ts,
            "pe1_fall_ts": self.pe1_fall_ts,
            "pe2_rise_ts": self.pe2_rise_ts,
            "pe2_fall_ts": self.pe2_fall_ts
        }

@dataclass
class SystemLayout:
    """系统布局配置"""
    # 光电位置（距离入口光电的距离，单位：mm）
    pe1_position_mm: float = 0.0  # 入口光电位置（参考点）
    pe2_position_mm: float = 1200.0  # 出口光电位置

    # 相机位置（距离入口光电的距离）
    camera1_position_mm: float = 400.0  # 相机1位置（近端）
    camera2_position_mm: float = 800.0  # 相机2位置（远端）

    # 传送带参数
    conveyor_length_mm: float = 1500.0  # 传送带总长度
    max_position_mm: float = 1500.0  # 最大位置（超过则认为已离开）

    # 速度估算参数
    default_speed_mm_s: float = 500.0  # 默认速度
    speed_estimation_window_s: float = 0.5  # 速度估算时间窗口


class ArchiveService:
    """
    鞋盒位置推算服务

    职责：
    1. 实时追踪每个鞋盒在传送带上的位置
    2. 根据光电信号和相机结果强制更新位置
    3. 支持MES客户端获取当前位置信息
    4. 定期清理已离开的鞋盒
    """

    def __init__(self, layout: SystemLayout = None):
        """
        初始化位置推算服务

        Args:
            layout: 系统布局配置
        """
        self.logger = logging.getLogger(__name__)
        self.layout = layout or SystemLayout()

        # 当前活动鞋盒位置信息
        self._active_boxes: Dict[str, BoxPosition] = {}

        # 已完成的鞋盒（用于归档）
        self._archived_boxes: List[BoxPosition] = []
        self._max_archive_size = 1000

        # 运行状态
        self._running = False
        self._update_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None

        # 更新间隔（秒）
        self._update_interval_s = 0.05  # 50ms更新一次

        # 事件回调
        self._on_position_update_callback = None

        self.logger.info(f"位置推算服务初始化完成，布局: PE1=0mm, PE2={self.layout.pe2_position_mm}mm, "
                         f"CAM1={self.layout.camera1_position_mm}mm, CAM2={self.layout.camera2_position_mm}mm")

    # =============================
    # 生命周期管理
    # =============================

    async def start(self) -> None:
        """启动位置推算服务"""
        if self._running:
            return

        self._running = True

        # 启动位置更新循环
        self._update_task = asyncio.create_task(self._position_update_loop())

        # 启动清理循环
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

        self.logger.info("位置推算服务已启动")

    async def stop(self) -> None:
        """停止位置推算服务"""
        self._running = False

        if self._update_task and not self._update_task.done():
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass

        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        self.logger.info("位置推算服务已停止")

    # =============================
    # 位置更新循环
    # =============================

    async def _position_update_loop(self) -> None:
        """位置更新循环 - 根据时间推移更新所有鞋盒位置"""
        while self._running:
            try:
                current_time = time.time()

                # 更新所有活动鞋盒的位置
                for track_id, box in list(self._active_boxes.items()):
                    if box.is_active and not box.has_exited:
                        # 根据速度和经过的时间计算新位置
                        elapsed = current_time - box.last_update
                        if elapsed > 0 and box.speed_mm_s > 0:
                            new_position = box.current_position_mm + box.speed_mm_s * elapsed
                            box.update_position(new_position, PositionSource.TIME_UPDATE, current_time)

                            # 更新到各点的距离
                            self._update_distances(box)

                            # 检查是否已离开传送带
                            if box.current_position_mm >= self.layout.max_position_mm:
                                box.has_exited = True
                                self.logger.info(f"鞋盒 {track_id} 已离开传送带，位置={box.current_position_mm:.1f}mm")

                await asyncio.sleep(self._update_interval_s)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"位置更新循环异常: {e}", exc_info=True)
                await asyncio.sleep(0.1)

    async def _cleanup_loop(self) -> None:
        """清理循环 - 移除已离开且超时的鞋盒"""
        cleanup_delay_s = 5.0  # 离开后5秒清理

        while self._running:
            try:
                await asyncio.sleep(1.0)

                current_time = time.time()
                to_remove = []

                for track_id, box in list(self._active_boxes.items()):
                    # 如果鞋盒已离开且超过清理时间，移动到归档
                    if box.has_exited and (current_time - box.last_update) > cleanup_delay_s:
                        to_remove.append(track_id)
                        self._archived_boxes.append(box)

                        # 限制归档大小
                        if len(self._archived_boxes) > self._max_archive_size:
                            self._archived_boxes = self._archived_boxes[-self._max_archive_size:]

                for track_id in to_remove:
                    del self._active_boxes[track_id]
                    self.logger.debug(f"清理已离开鞋盒: {track_id}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"清理循环异常: {e}", exc_info=True)

    # =============================
    # 事件处理（强制更新）
    # =============================

    def on_pe_rise(self, track_id: str, sensor: str, timestamp: float) -> Optional[BoxPosition]:
        """
        处理PE上升沿事件 - 强制更新鞋盒位置

        Args:
            track_id: 轨迹ID
            sensor: 传感器名称 (PE1/PE2)
            timestamp: 事件时间戳

        Returns:
            更新后的鞋盒位置对象
        """
        if sensor == "PE1":
            return self._handle_pe1_rise(track_id, timestamp)
        elif sensor == "PE2":
            return self._handle_pe2_rise(track_id, timestamp)

        return None

    def on_pe_fall(self, track_id: str, sensor: str, timestamp: float) -> Optional[BoxPosition]:
        """
        处理PE下降沿事件 - 强制更新鞋盒位置并估算速度

        Args:
            track_id: 轨迹ID
            sensor: 传感器名称 (PE1/PE2)
            timestamp: 事件时间戳

        Returns:
            更新后的鞋盒位置对象
        """
        if sensor == "PE1":
            return self._handle_pe1_fall(track_id, timestamp)
        elif sensor == "PE2":
            return self._handle_pe2_fall(track_id, timestamp)

        return None

    def on_camera_result(self, track_id: str, camera_id: int, result: dict) -> Optional[BoxPosition]:
        """
        处理相机识别结果 - 强制更新位置和结果

        Args:
            track_id: 轨迹ID
            camera_id: 相机ID (1或2)
            result: 识别结果

        Returns:
            更新后的鞋盒位置对象
        """
        return self._handle_camera_result(track_id, camera_id, result)

    def _handle_pe1_rise(self, track_id: str, timestamp: float) -> BoxPosition:
        """处理入口光电上升沿 - 鞋盒进入"""
        # 创建新鞋盒位置记录
        box = BoxPosition(
            track_id=track_id,
            created_at=timestamp,
            last_update=timestamp,
            current_position_mm=self.layout.pe1_position_mm,
            pe1_rise_ts=timestamp,
            is_active=True,
            has_entered=True,
            speed_mm_s=self.layout.default_speed_mm_s
        )

        # 更新到各点的距离
        self._update_distances(box)

        # 添加到活动列表
        self._active_boxes[track_id] = box

        self.logger.info(f"[位置推算] 鞋盒 {track_id} 进入，位置={box.current_position_mm:.1f}mm，"
                         f"距离相机1={box.distance_to_camera1_mm:.1f}mm")

        # 触发回调
        self._on_position_update(box, "enter")

        return box

    def _handle_pe1_fall(self, track_id: str, timestamp: float) -> Optional[BoxPosition]:
        """处理入口光电下降沿 - 鞋盒尾部通过，估算长度"""
        box = self._active_boxes.get(track_id)
        if not box:
            self.logger.warning(f"[位置推算] 鞋盒 {track_id} 不存在，无法处理PE1下降沿")
            return None

        box.pe1_fall_ts = timestamp

        # 估算鞋盒长度（速度 * 通过时间）
        if box.pe1_rise_ts and box.speed_mm_s > 0:
            elapsed = timestamp - box.pe1_rise_ts
            box.length_mm = box.speed_mm_s * elapsed

            # 更新当前位置（尾部位置）
            new_position = self.layout.pe1_position_mm + box.length_mm
            box.update_position(new_position, PositionSource.PE_FALL, timestamp)
            self._update_distances(box)

            self.logger.info(f"[位置推算] 鞋盒 {track_id} 尾部通过PE1，长度={box.length_mm:.1f}mm，"
                             f"位置={box.current_position_mm:.1f}mm")

        self._on_position_update(box, "pe1_fall")
        return box

    def _handle_pe2_rise(self, track_id: str, timestamp: float) -> Optional[BoxPosition]:
        """处理出口光电上升沿 - 鞋盒到达出口"""
        box = self._active_boxes.get(track_id)
        if not box:
            self.logger.warning(f"[位置推算] 鞋盒 {track_id} 不存在，无法处理PE2上升沿")
            return None

        box.pe2_rise_ts = timestamp

        # 强制更新位置到PE2位置
        box.update_position(self.layout.pe2_position_mm, PositionSource.PE_RISE, timestamp)

        # 重新计算速度（基于PE1到PE2的实际时间）
        if box.pe1_rise_ts:
            elapsed = timestamp - box.pe1_rise_ts
            if elapsed > 0:
                actual_speed = self.layout.pe2_position_mm / elapsed
                box.speed_mm_s = actual_speed
                self.logger.info(f"[位置推算] 鞋盒 {track_id} 到达PE2，实测速度={actual_speed:.1f}mm/s")

        self._update_distances(box)
        self.logger.info(f"[位置推算] 鞋盒 {track_id} 到达出口，位置={box.current_position_mm:.1f}mm")

        self._on_position_update(box, "pe2_rise")
        return box

    def _handle_pe2_fall(self, track_id: str, timestamp: float) -> Optional[BoxPosition]:
        """处理出口光电下降沿 - 鞋盒尾部通过出口"""
        box = self._active_boxes.get(track_id)
        if not box:
            return None

        box.pe2_fall_ts = timestamp

        # 标记为已离开（但保留一段时间用于归档）
        box.has_exited = True

        self.logger.info(f"[位置推算] 鞋盒 {track_id} 完全离开")
        self._on_position_update(box, "exit")

        return box

    def _handle_camera_result(self, track_id: str, camera_id: int, result: dict) -> Optional[BoxPosition]:
        """处理相机识别结果"""
        box = self._active_boxes.get(track_id)
        if not box:
            self.logger.warning(f"[位置推算] 鞋盒 {track_id} 不存在，无法处理相机结果")
            return None

        # 强制更新位置到对应相机位置
        if camera_id == 1:
            box.update_position(self.layout.camera1_position_mm, PositionSource.CAMERA_RESULT)
            box.camera1_result = result.get("code")
            self.logger.info(f"[位置推算] 鞋盒 {track_id} 在相机1位置被识别: {box.camera1_result}")
        elif camera_id == 2:
            box.update_position(self.layout.camera2_position_mm, PositionSource.CAMERA_RESULT)
            box.camera2_result = result.get("code")
            self.logger.info(f"[位置推算] 鞋盒 {track_id} 在相机2位置被识别: {box.camera2_result}")

        # 更新最终结果
        if result.get("result") == "OK":
            box.final_code = result.get("code")
            box.final_status = "OK"

        self._update_distances(box)
        self._on_position_update(box, f"camera_{camera_id}")

        return box

    def _update_distances(self, box: BoxPosition) -> None:
        """更新到各点的距离"""
        box.distance_to_camera1_mm = abs(self.layout.camera1_position_mm - box.current_position_mm)
        box.distance_to_camera2_mm = abs(self.layout.camera2_position_mm - box.current_position_mm)
        box.distance_to_exit_mm = abs(self.layout.max_position_mm - box.current_position_mm)

    # =============================
    # 查询接口（供MES客户端调用）
    # =============================

    def get_current_position(self, track_id: str = None) -> Optional[Dict]:
        """
        获取鞋盒当前位置

        Args:
            track_id: 轨迹ID，为None时返回所有活动鞋盒

        Returns:
            位置信息字典
        """
        if track_id:
            box = self._active_boxes.get(track_id)
            return box.to_dict() if box else None

        return [box.to_dict() for box in self._active_boxes.values()]

    def get_all_active_positions(self) -> List[Dict]:
        """获取所有活动鞋盒的位置"""
        return [box.to_dict() for box in self._active_boxes.values()]

    def get_positions_snapshot(self) -> Dict:
        """获取位置快照（用于MES上报）"""
        return {
            "timestamp": time.time(),
            "active_count": len(self._active_boxes),
            "boxes": [box.to_dict() for box in self._active_boxes.values()]
        }

    def get_box_by_id(self, track_id: str) -> Optional[BoxPosition]:
        """根据ID获取鞋盒位置对象"""
        return self._active_boxes.get(track_id)

    def get_box_by_position_range(self, start_mm: float, end_mm: float) -> List[BoxPosition]:
        """获取在指定位置范围内的鞋盒"""
        result = []
        for box in self._active_boxes.values():
            if start_mm <= box.current_position_mm <= end_mm:
                result.append(box)
        return result

    # =============================
    # 速度估算
    # =============================

    def estimate_speed(self, track_id: str) -> Optional[float]:
        """估算指定鞋盒的速度"""
        box = self._active_boxes.get(track_id)
        if not box:
            return None

        # 如果有PE1和PE2时间，使用精确速度
        if box.pe1_rise_ts and box.pe2_rise_ts:
            elapsed = box.pe2_rise_ts - box.pe1_rise_ts
            if elapsed > 0:
                return self.layout.pe2_position_mm / elapsed

        # 否则返回当前记录的速度
        return box.speed_mm_s

    # =============================
    # 预测
    # =============================

    def predict_arrival_time(self, track_id: str, target_position_mm: float) -> Optional[float]:
        """
        预测鞋盒到达指定位置的时间

        Args:
            track_id: 轨迹ID
            target_position_mm: 目标位置（mm）

        Returns:
            预计到达时间戳，如果无法预测返回None
        """
        box = self._active_boxes.get(track_id)
        if not box or box.speed_mm_s <= 0:
            return None

        distance = target_position_mm - box.current_position_mm
        if distance <= 0:
            return time.time()  # 已经在目标位置或之后

        travel_time = distance / box.speed_mm_s
        return time.time() + travel_time

    def predict_camera_arrival(self, track_id: str, camera_id: int) -> Optional[float]:
        """预测鞋盒到达指定相机的时间"""
        if camera_id == 1:
            target = self.layout.camera1_position_mm
        elif camera_id == 2:
            target = self.layout.camera2_position_mm
        else:
            return None

        return self.predict_arrival_time(track_id, target)

    # =============================
    # 工具方法
    # =============================

    def _on_position_update(self, box: BoxPosition, event_type: str):
        """位置更新回调"""
        if self._on_position_update_callback:
            try:
                self._on_position_update_callback(box, event_type)
            except Exception as e:
                self.logger.error(f"位置更新回调异常: {e}")

    def set_position_update_callback(self, callback):
        """设置位置更新回调"""
        self._on_position_update_callback = callback

    def get_stats(self) -> Dict:
        """获取服务统计信息"""
        return {
            "active_count": len(self._active_boxes),
            "archived_count": len(self._archived_boxes),
            "layout": {
                "pe1_position": self.layout.pe1_position_mm,
                "pe2_position": self.layout.pe2_position_mm,
                "camera1_position": self.layout.camera1_position_mm,
                "camera2_position": self.layout.camera2_position_mm,
                "conveyor_length": self.layout.conveyor_length_mm
            },
            "update_interval_s": self._update_interval_s,
            "is_running": self._running
        }

    def clear_archived(self) -> int:
        """清空归档的鞋盒记录"""
        count = len(self._archived_boxes)
        self._archived_boxes.clear()
        return count