# services/archive_service.py
"""
鞋盒位置跟踪和路径规划服务

整合 ArchiveService 和 PathPlanner 的功能：
1. PE1/PE2 触发时立即创建跟踪（位置推算）
2. 实时推算每个鞋盒的当前位置
3. 扫码成功后规划目标路径
4. 到达摆轮机前500mm发送TCP信号
"""

import asyncio
import time
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from collections import deque
from enum import Enum
from http.server import HTTPServer, BaseHTTPRequestHandler

from config.path_config import DEFAULT_DIVERT_UNITS, DEFAULT_PATHS, PathConfig, PathType
from devices.plc_client import PlcDivertClient
from domain import TrackStatus
from domain.models import BoxTrack
from config.manager import get_config
from services.event_bus import EventBus


@dataclass
class BoxTrackingData:
    """
    鞋盒完整跟踪数据

    整合了位置信息和路径规划信息
    """
    track_id: str

    # 位置信息（来自 ArchiveService）
    current_pos_mm: float = 0.0  # 当前位置（毫米，以 PE1 为原点）
    speed_mm_s: float = 0.0  # 当前速度（毫米/秒）
    last_update_ms: float = 0.0  # 最后更新时间
    has_exited: bool = False  # 是否已离开
    created_ms: float = 0.0  # 创建时间
    pe1_on_ms: float = 0.0  # PE1 触发时间
    pe2_on_ms: float = 0.0  # PE2 触发时间
    length_mm: float = 0.0  # 鞋盒长度（估算）

    # 路径规划信息（来自 PathPlanner）
    path_id: int = -1  # 目标路径ID（-1表示未分配）
    path_config: Optional[PathConfig] = None  # 路径配置
    target_divert_id: Optional[int] = None  # 目标摆轮机ID
    divert_triggered: List[int] = field(default_factory=list)  # 已触发的摆轮机

    # 状态
    status: TrackStatus = TrackStatus.PENDING

    # TCP 触发标记
    tcp_sent: bool = False  # 是否已发送 TCP 信号

    def to_dict(self) -> dict:
        """转换为字典格式"""
        return {
            "track_id": self.track_id,
            "current_pos_mm": round(self.current_pos_mm, 1),
            "speed_mm_s": round(self.speed_mm_s, 1),
            "status": self.status.value,
            "path_id": self.path_id,
            "target_divert_id": self.target_divert_id,
            "has_exited": self.has_exited,
            "created_ms": self.created_ms,
            "pe1_on_ms": self.pe1_on_ms,
            "pe2_on_ms": self.pe2_on_ms,
            "length_mm": round(self.length_mm, 1),
            "last_update_ms": self.last_update_ms
        }


def _code_to_path(code: str) -> Optional[int]:
    """
    将扫码码值映射到路径ID

    Args:
        code: 扫码码值

    Returns:
        路径ID，失败返回 None
    """
    try:
        code_num = int(code) % 4 + 1
        if 1 <= code_num <= 4:
            return code_num
    except ValueError:
        pass

    # 可以根据其他规则扩展
    return None


def _get_divert_for_path(path: PathConfig) -> Optional[int]:
    """
    根据路径配置获取对应的摆轮机ID

    Args:
        path: 路径配置

    Returns:
        摆轮机ID
    """
    if not path.divert_units:
        return None

    # 返回该路径上的第一个摆轮机
    # 可以根据业务规则选择不同的摆轮机
    return path.divert_units[0]


class ArchiveService:
    """
    鞋盒位置跟踪和路径规划服务

    整合功能：
    1. 根据 PE1/PE2 触发事件创建/更新鞋盒跟踪
    2. 基于传送带速度实时推算每个鞋盒的当前位置
    3. 扫码成功后规划目标路径和摆轮机
    4. 到达摆轮机前500mm发送TCP信号
    5. 提供 HTTP 接口供外部系统查询鞋盒位置
    """

    def __init__(self, event_bus: EventBus = None):
        self.logger = logging.getLogger(__name__)

        # ========== 物理参数配置 ==========
        self.pe1_pos_mm = 0.0  # PE1 位置（原点）
        self.pe2_pos_mm = get_config("pe1_to_pe2_dist") * 1000  # PE2 位置
        self.camera_pos_mm = self.pe2_pos_mm + get_config("pe2_to_camera_dist") * 1000  # 相机位置

        # 传送带参数
        self.conveyor_speed_mm_s = get_config("conveyor.default_speed_mm_s", 500.0)
        self.max_pos_mm = self.camera_pos_mm + 5000  # 最大跟踪位置（相机后方 5米）

        # 摆轮机触发距离（前500mm）
        self.TRIGGER_DISTANCE_MM = 500

        # ========== 路径和摆轮机配置 ==========
        self.paths = DEFAULT_PATHS
        self.divert_units = DEFAULT_DIVERT_UNITS

        # 摆轮机位置映射（用于快速查询）
        self.divert_position_map: Dict[int, float] = {
            divert_id: divert.position_mm
            for divert_id, divert in self.divert_units.items()
        }

        # 摆轮机对应的路径类型
        self.divert_path_map: Dict[int, PathType] = {
            divert_id: divert.path_type
            for divert_id, divert in self.divert_units.items()
        }

        # ========== 跟踪数据 ==========
        self._active_boxes: Dict[str, BoxTrackingData] = {}  # 活动鞋盒
        self._finished_boxes: List[BoxTrackingData] = []  # 已完成鞋盒
        self._max_finished = 1000

        # 按位置排序的队列
        self._box_queue = deque()

        # ========== 运行状态 ==========
        self._running = False
        self._position_task: Optional[asyncio.Task] = None
        self._divert_monitor_task: Optional[asyncio.Task] = None

        # # ========== TCP 配置 ==========
        # self._tcp_host = get_config("divert.tcp_host")
        # self._tcp_port = get_config("divert.tcp_port")
        # self._tcp_writer: Optional[asyncio.StreamWriter] = None
        self._plc_client: Optional[PlcDivertClient] = None
        self.event_bus = event_bus


        # ========== 统计信息 ==========
        self._stats = {
            "total_boxes": 0,
            "active_count": 0,
            "finished_count": 0,
            "exited_count": 0,
            "divert_triggered": 0,
            "tcp_sent": 0
        }

        self.logger.info(f"BoxTracker 初始化完成: PE1={self.pe1_pos_mm}mm, "
                         f"PE2={self.pe2_pos_mm}mm, 相机={self.camera_pos_mm}mm")

    def get_queue_status(self) -> dict:
        """获取队列状态信息"""
        now_ms = time.time_ns() / 1_000_000

        queue_items = []
        for box in self._active_boxes.values():
            # 计算实时位置
            elapsed_s = (now_ms - box.last_update_ms) / 1000.0
            current_pos = box.current_pos_mm + (box.speed_mm_s * elapsed_s)

            queue_items.append({
                "track_id": box.track_id,
                "position": round(current_pos, 1),
                "speed": round(box.speed_mm_s, 1),
                "status": box.status.value,
                "target_divert": box.target_divert_id,
                "created_ms": box.created_ms,
                "age_ms": round(now_ms - box.created_ms, 0)
            })

        # 按位置排序
        queue_items.sort(key=lambda x: x["position"])

        return {
            "active_count": len(self._active_boxes),
            "finished_count": len(self._finished_boxes),
            "queue": queue_items,
            "head_box": queue_items[-1] if queue_items else None,
            "tail_box": queue_items[0] if queue_items else None,
            "timestamp": now_ms
        }

    def print_queue(self) -> None:
        """打印队列状态到控制台"""
        status = self.get_queue_status()

        self.logger.info("=" * 60)
        self.logger.info(f"📦 鞋盒队列状态 | 活动: {status['active_count']} | 已完成: {status['finished_count']}")
        self.logger.info("-" * 60)

        if not status['queue']:
            self.logger.info("队列为空")
        else:
            self.logger.info(f"{'位置(mm)':<12} {'轨迹ID':<25} {'速度':<10} {'状态':<12} {'目标摆轮机'}")
            self.logger.info("-" * 60)

            for item in status['queue']:
                self.logger.info(
                    f"{item['position']:<12.1f} "
                    f"{item['track_id']:<25} "
                    f"{item['speed']:<10.1f} "
                    f"{item['status']:<12} "
                    f"{item['target_divert'] or '-'}"
                )

            if status['head_box']:
                self.logger.info("-" * 60)
                self.logger.info(f"📍 头盒: {status['head_box']['track_id']} @ {status['head_box']['position']:.1f}mm")
                self.logger.info(f"📍 尾盒: {status['tail_box']['track_id']} @ {status['tail_box']['position']:.1f}mm")

        self.logger.info("=" * 60)

    # =============================
    # 生命周期管理
    # =============================

    async def start(self) -> None:
        """启动跟踪服务"""
        if self._running:
            self.logger.warning("BoxTracker 已经在运行")
            return

        self._running = True
        self.logger.info("BoxTracker 启动")

        # 启动位置推算循环
        self._position_task = asyncio.create_task(self._position_loop())

        # 启动摆轮机触发监控循环
        self._divert_monitor_task = asyncio.create_task(self._divert_monitor_loop())

        # # 建立 TCP 连接
        # await self._connect_tcp()

        # 初始化 PLC 客户端
        self._plc_client = PlcDivertClient(self.event_bus)
        try:
            await self._plc_client.connect()
            self.logger.info("PLC 摆轮机客户端已启动")
        except Exception as e:
            self.logger.error(f"PLC 连接失败: {e}")

        self.logger.info(f"BoxTracker 启动完成")

    async def stop(self) -> None:
        """停止跟踪服务"""
        if not self._running:
            return

        self._running = False
        self.logger.info("BoxTracker 停止中...")

        # 停止位置推算循环
        if self._position_task:
            self._position_task.cancel()
            try:
                await self._position_task
            except asyncio.CancelledError:
                pass

        # 停止摆轮机监控循环
        if self._divert_monitor_task:
            self._divert_monitor_task.cancel()
            try:
                await self._divert_monitor_task
            except asyncio.CancelledError:
                pass

        # 关闭 PLC 连接
        if self._plc_client:
            await self._plc_client.disconnect()

        self.logger.info("BoxTracker 已停止")

    async def _send_divert_signal(self, divert_id: int, direction: int) -> bool:
        """
        发送转向信号到摆轮机

        Args:
            divert_id: 摆轮机ID（1-4对应方向1-4）
            direction: 方向编号

        Returns:
            是否发送成功
        """
        if self._plc_client and self._plc_client.is_connected:
            self.logger.info(f"📡 [PLC] 发送转向信号: 摆轮机 {divert_id}, 方向 {direction}")
            success = await self._plc_client.set_direction(direction)
            if success:
                self._stats["tcp_sent"] += 1
            return success
        else:
            self.logger.warning(f"PLC 未连接，无法发送转向信号")
            return False

    # =============================
    # 位置推算循环
    # =============================

    async def _position_loop(self) -> None:
        """
        位置推算主循环

        定期更新所有活动鞋盒的位置
        """
        interval = get_config("archive_service.update_interval_ms", 20) / 1000.0  # 默认 20ms

        self.logger.info(f"位置推算循环启动，更新间隔={interval * 1000:.1f}ms")

        while self._running:
            try:
                now_ms = time.time_ns() / 1_000_000
                # 更新所有活动鞋盒的位置
                for box in list(self._active_boxes.values()):
                    if box.has_exited:
                        continue

                    # 计算经过的时间（毫秒）
                    elapsed_ms = now_ms - box.last_update_ms
                    if elapsed_ms <= 0:
                        continue

                    # 推算新位置
                    elapsed_s = elapsed_ms / 1000.0
                    delta_pos = box.speed_mm_s * elapsed_s
                    box.current_pos_mm += delta_pos
                    box.last_update_ms = now_ms

                    # 检查是否已离开传送带
                    if box.current_pos_mm >= self.max_pos_mm:
                        box.has_exited = True
                        box.status = TrackStatus.FINALIZED
                        self._stats["exited_count"] += 1
                        self.logger.debug(f"鞋盒 {box.track_id} 已离开传送带，"
                                          f"最后位置={box.current_pos_mm:.1f}mm")

                # 更新统计
                self._stats["active_count"] = len(self._active_boxes)
                self._stats["finished_count"] = len(self._finished_boxes)

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"位置推算循环异常: {e}", exc_info=True)
                # 检查是否是事件循环错误
                if "no running event loop" in str(e):
                    # 尝试重新获取事件循环
                    try:
                        loop = asyncio.get_event_loop()
                        self.logger.info("重新获取事件循环成功")
                    except:
                        self.logger.warning("无法获取事件循环，等待后重试")
                await asyncio.sleep(0.1)

        self.logger.info("位置推算循环结束")

    # =============================
    # 摆轮机触发监控循环
    # =============================
    def _get_direction_from_box(self, box: BoxTrackingData) -> Optional[int]:
        """
        根据鞋盒信息获取摆轮机方向

        根据通讯地址表：
        - 方向1: 写入1
        - 方向2: 写入2
        - 方向3: 写入3
        - 方向4: 写入4
        """
        # 方式1：使用路径ID
        if box.path_id:
            return box.path_id  # 路径ID 1-4 对应方向 1-4

        # 方式2：从最终码值解析
        # 可以从 track 中获取 final_code
        # return self._code_to_direction(box.final_code)

        return None

    async def _divert_monitor_loop(self) -> None:
        """
        摆轮机触发监控循环

        检查每个鞋盒是否到达目标摆轮机前500mm，如果是则发送 TCP 信号
        """
        interval = 0.02  # 20ms 检查一次

        self.logger.info(f"摆轮机监控循环启动，触发距离={self.TRIGGER_DISTANCE_MM}mm")

        while self._running:
            try:
                now_ms = time.time_ns() / 1_000_000

                for box in list(self._active_boxes.values()):
                    # 只处理已分配路径且未触发的鞋盒
                    if box.status != TrackStatus.WAITING_DIVERT:
                        continue

                    if box.target_divert_id is None:
                        continue

                    if box.tcp_sent:
                        continue

                    # 获取目标摆轮机位置
                    divert_pos = self.divert_position_map.get(box.target_divert_id)
                    if divert_pos is None:
                        continue

                    # 计算当前位置（使用实时位置）
                    elapsed_s = (now_ms - box.last_update_ms) / 1000.0
                    current_pos = box.current_pos_mm + (box.speed_mm_s * elapsed_s)

                    # 触发位置 = 摆轮机位置 - 500mm
                    trigger_pos = divert_pos - self.TRIGGER_DISTANCE_MM

                    # 检查是否到达触发点
                    if current_pos >= trigger_pos:
                        direction = self._get_direction_from_box(box)

                        self.logger.info(f"🔔 [触发] 鞋盒 {box.track_id} 到达摆轮机 {box.target_divert_id} 前500mm, "
                                         f"当前位置={current_pos:.1f}mm, 触发位置={trigger_pos:.1f}mm")

                        # 发送 PLC 信号
                        success = await self._send_divert_signal(
                            box.target_divert_id,
                            direction
                        )

                        if success:
                            box.tcp_sent = True
                            box.status = TrackStatus.DIVERT_TRIGGERED
                            self._stats["divert_triggered"] += 1

                            # 记录触发时间
                            if box.target_divert_id not in box.divert_triggered:
                                box.divert_triggered.append(box.target_divert_id)

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"摆轮机监控循环异常: {e}", exc_info=True)
                await asyncio.sleep(0.1)

        self.logger.info("摆轮机监控循环结束")

    # =============================
    # 光电事件处理
    # =============================

    def handle_on_pe1(self, track: BoxTrack) -> None:
        """
        处理 PE1 上升沿事件（鞋盒进入入口）

        Args:
            track: 鞋盒轨迹对象
        """
        now_ms = time.time_ns() / 1_000_000

        # 创建跟踪数据
        box = BoxTrackingData(
            track_id=track.track_id,
            current_pos_mm=self.pe1_pos_mm,
            speed_mm_s=track.speed_mm_s or self.conveyor_speed_mm_s,
            last_update_ms=now_ms,
            has_exited=False,
            created_ms=now_ms,
            pe1_on_ms=track.pe1_on_ms or now_ms,
            pe2_on_ms=track.pe2_on_ms or 0,
            length_mm=track.length_mm or 0,
            status=TrackStatus.PENDING
        )

        self._active_boxes[track.track_id] = box
        self._update_queue()
        self._stats["total_boxes"] += 1

        self.logger.info(f"[PE1] 创建鞋盒跟踪: {track.track_id}, "
                         f"速度={box.speed_mm_s:.1f}mm/s")

    def handle_on_pe2(self, track: BoxTrack) -> None:
        """
        处理 PE2 上升沿事件（鞋盒到达出口）

        在 PE2 触发时，获取更精确的速度和位置信息

        Args:
            track: 鞋盒轨迹对象
        """
        now_ms = time.time_ns() / 1_000_000

        box = self._active_boxes.get(track.track_id)
        if not box:
            self.logger.warning(f"[PE2] 未找到鞋盒跟踪: {track.track_id}")
            return

        # 更新位置为 PE2
        box.current_pos_mm = self.pe2_pos_mm
        box.last_update_ms = now_ms
        box.pe2_on_ms = track.pe2_on_ms or now_ms

        # 如果有更精确的速度，更新速度
        if track.speed_mm_s and track.speed_mm_s > 0:
            box.speed_mm_s = track.speed_mm_s

        # 估算鞋盒长度
        if box.pe1_on_ms > 0 and box.pe2_on_ms > 0:
            time_diff_s = (box.pe2_on_ms - box.pe1_on_ms) / 1000.0
            if time_diff_s > 0:
                box.length_mm = box.speed_mm_s * time_diff_s

        self.logger.info(f"[PE2] 更新鞋盒跟踪: {track.track_id}, "
                         f"位置={box.current_pos_mm:.1f}mm, 速度={box.speed_mm_s:.1f}mm/s")

    # =============================
    # 扫码结果处理（路径规划）
    # =============================

    def handle_scan_result(self, track_id: str, code: str) -> Optional[int]:
        """
        处理扫码结果，规划目标路径和摆轮机

        Args:
            track_id: 轨迹ID
            code: 扫码码值

        Returns:
            分配的摆轮机ID，失败返回 None
        """
        box = self._active_boxes.get(track_id)
        if not box:
            self.logger.warning(f"扫码结果: 未找到鞋盒 {track_id}")
            return None

        # 1. 根据码值分配路径
        path_id = _code_to_path(code)
        if path_id is None:
            self.logger.warning(f"码值 {code} 无法映射到有效路径")
            return None

        print(self.paths)
        path = self.paths.get(path_id)
        if not path:
            self.logger.error(f"路径 {path_id} 不存在")
            return None

        # 2. 获取该路径上的摆轮机
        # 根据业务规则选择摆轮机（这里简单取第一个）
        target_divert_id = _get_divert_for_path(path)
        if target_divert_id is None:
            self.logger.warning(f"路径 {path_id} 没有配置摆轮机")
            return None

        # 3. 更新跟踪数据
        box.path_id = path_id
        box.path_config = path
        box.target_divert_id = target_divert_id
        box.status = TrackStatus.WAITING_DIVERT

        self.logger.info(f"[扫码] 鞋盒 {track_id} 规划完成: "
                         f"码值={code}, 路径={path_id}, 摆轮机={target_divert_id}")

        return target_divert_id

    # =============================
    # 队列管理
    # =============================

    def _update_queue(self) -> None:
        """更新队列排序（按位置）"""
        self._box_queue = deque(sorted(
            self._active_boxes.values(),
            key=lambda b: b.current_pos_mm
        ))

    def get_head_box(self) -> Optional[BoxTrackingData]:
        """获取头盒（最前面的鞋盒）"""
        if self._box_queue:
            return self._box_queue[-1]
        return None

    def get_tail_box(self) -> Optional[BoxTrackingData]:
        """获取尾盒（最后面的鞋盒）"""
        if self._box_queue:
            return self._box_queue[0]
        return None

    def get_boxes_before_position(self, position_mm: float) -> List[BoxTrackingData]:
        """获取位置在指定点之前的鞋盒"""
        return [b for b in self._active_boxes.values()
                if b.current_pos_mm < position_mm]

    def get_boxes_after_position(self, position_mm: float) -> List[BoxTrackingData]:
        """获取位置在指定点之后的鞋盒"""
        return [b for b in self._active_boxes.values()
                if b.current_pos_mm > position_mm]

    # =============================
    # 位置查询接口
    # =============================

    def get_position(self, track_id: str) -> Optional[BoxTrackingData]:
        """
        获取鞋盒当前位置

        Args:
            track_id: 轨迹ID

        Returns:
            鞋盒跟踪数据，不存在则返回 None
        """
        # 先查活动列表
        box = self._active_boxes.get(track_id)
        if box:
            return box

        # 再查已完成列表
        for box in self._finished_boxes:
            if box.track_id == track_id:
                return box

        return None

    def get_all_active_positions(self) -> List[dict]:
        """
        获取所有活动鞋盒的位置

        Returns:
            位置信息列表
        """
        now_ms = time.time_ns() / 1_000_000

        result = []
        for box in self._active_boxes.values():
            if box.has_exited:
                continue

            # 实时计算最新位置
            elapsed_s = (now_ms - box.last_update_ms) / 1000.0
            current_pos = box.current_pos_mm + (box.speed_mm_s * elapsed_s)

            result.append({
                "track_id": box.track_id,
                "current_pos_mm": round(current_pos, 1),
                "speed_mm_s": round(box.speed_mm_s, 1),
                "status": box.status.value,
                "target_divert_id": box.target_divert_id,
                "estimated_time_to_camera_ms": max(0, (self.camera_pos_mm - current_pos) / box.speed_mm_s * 1000)
                if box.speed_mm_s > 0 else -1,
                "estimated_time_to_divert_ms": self._get_time_to_divert(box, current_pos)
            })

        return result

    def _get_time_to_divert(self, box: BoxTrackingData, current_pos: float) -> float:
        """计算到达目标摆轮机的时间（毫秒）"""
        if box.target_divert_id is None:
            return -1

        divert_pos = self.divert_position_map.get(box.target_divert_id)
        if divert_pos is None or box.speed_mm_s <= 0:
            return -1

        distance = divert_pos - current_pos
        if distance <= 0:
            return 0

        return (distance / box.speed_mm_s) * 1000

    # =============================
    # 清理方法
    # =============================

    def _complete_box(self, track_id: str) -> None:
        """完成鞋盒跟踪"""
        box = self._active_boxes.pop(track_id, None)
        if box:
            box.status = TrackStatus.FINALIZED
            self._finished_boxes.append(box)

            # 清理过多的已完成记录
            if len(self._finished_boxes) > self._max_finished:
                self._finished_boxes = self._finished_boxes[-self._max_finished:]

            self._update_queue()
            self.logger.info(f"鞋盒 {track_id} 已完成跟踪")

    def clear_finished(self) -> None:
        """清理所有已完成的鞋盒记录"""
        count = len(self._finished_boxes)
        self._finished_boxes.clear()
        self.logger.info(f"清理了 {count} 个已完成鞋盒记录")

    def reset(self) -> None:
        """重置所有状态"""
        self._active_boxes.clear()
        self._finished_boxes.clear()
        self._box_queue.clear()
        self._stats = {
            "total_boxes": 0,
            "active_count": 0,
            "finished_count": 0,
            "exited_count": 0,
            "divert_triggered": 0,
            "tcp_sent": 0
        }
        self.logger.info("BoxTracker 已重置")

    # =============================
    # 统计信息
    # =============================

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            **self._stats,
            "max_pos_mm": self.max_pos_mm,
            "pe1_pos_mm": self.pe1_pos_mm,
            "pe2_pos_mm": self.pe2_pos_mm,
            "camera_pos_mm": self.camera_pos_mm,
            "trigger_distance_mm": self.TRIGGER_DISTANCE_MM,
            "conveyor_speed_mm_s": self.conveyor_speed_mm_s,
            "tcp_host": self._tcp_host,
            "tcp_port": self._tcp_port
        }
