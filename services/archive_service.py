# services/archive_service.py
"""
鞋盒位置跟踪和路径规划服务

整合 ArchiveService 和 PathPlanner 的功能：
1. PE1/PE2 触发时立即创建跟踪（位置推算）
2. 实时推算每个鞋盒的当前位置
3. 扫码成功后规划目标路径
4. 到达摆轮机时发送PLC信号
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

from pymodbus.client import AsyncModbusTcpClient

from config.path_config import DEFAULT_DIVERT_UNITS, DEFAULT_PATHS, PathConfig, PathType
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

    # PLC 触发标记
    plc_triggered: bool = False  # 是否已发送 PLC 信号


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
    return path.divert_units[0]


class ArchiveService:
    """
    鞋盒位置跟踪和路径规划服务

    整合功能：
    1. 根据 PE1/PE2 触发事件创建/更新鞋盒跟踪
    2. 基于传送带速度实时推算每个鞋盒的当前位置
    3. 扫码成功后规划目标路径和摆轮机
    4. 到达摆轮机时发送PLC信号
    """

    def __init__(self, event_bus: EventBus = None):
        self.logger = logging.getLogger(__name__)

        # ========== 物理参数配置 ==========
        self.pe1_pos_mm = 0.0  # PE1 位置（原点）
        self.pe2_pos_mm = get_config("pe1_to_pe2_dist") * 1000  # PE2 位置
        self.camera_pos_mm = self.pe2_pos_mm + get_config("pe2_to_camera_dist") * 1000  # 相机位置

        # 传送带参数
        self.conveyor_speed_mm_s = get_config("conveyor.default_speed_mm_s", 500.0)
        self.max_pos_mm = self.camera_pos_mm + 5000

        # ========== PLC 参数（根据真实接口） ==========
        self.plc_ip = get_config("plc.ip", "192.168.1.200")
        self.plc_port = get_config("plc.port", 502)

        # 寄存器地址
        self.D0_ADDR = get_config("plc.d0_addr", 0)  # 摆轮机1控制
        self.D1_ADDR = get_config("plc.d1_addr", 1)  # 摆轮机2控制

        # 延迟时间（从光电门到摆轮机的时间，单位：秒）
        self.T_D0 = get_config("plc.t_d0", 1.788)  # 到摆轮机1的时间
        self.T_D1 = get_config("plc.t_d1", 3.9285)  # 到摆轮机2的时间

        # 防抖时间（毫秒）
        self.TIME_BIT = get_config("plc.time_bit", 500)

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
        self._active_boxes: Dict[str, BoxTrackingData] = {}
        self._finished_boxes: List[BoxTrackingData] = []
        self._max_finished = 1000

        # 按位置排序的队列
        self._box_queue = deque()

        # ========== 运行状态 ==========
        self._running = False
        self._position_task: Optional[asyncio.Task] = None
        self._divert_monitor_task: Optional[asyncio.Task] = None

        # PLC 客户端
        self._plc_client: Optional[AsyncModbusTcpClient] = None
        self._plc_connected = False

        self.event_bus = event_bus

        # ========== 统计信息 ==========
        self._stats = {
            "total_boxes": 0,
            "active_count": 0,
            "finished_count": 0,
            "exited_count": 0,
            "divert_triggered": 0,
            "plc_sent": 0
        }

        self.logger.info(f"ArchiveService 初始化完成: PE1={self.pe1_pos_mm}mm, "
                         f"PE2={self.pe2_pos_mm}mm, 相机={self.camera_pos_mm}mm")
        self.logger.info(f"PLC 参数: T_D0={self.T_D0}s, T_D1={self.T_D1}s")

    # =============================
    # 生命周期管理
    # =============================

    async def start(self) -> None:
        """启动跟踪服务"""
        if self._running:
            self.logger.warning("ArchiveService 已经在运行")
            return

        self._running = True
        self.logger.info("ArchiveService 启动")

        # 连接 PLC
        await self._connect_plc()

        # 启动位置推算循环
        self._position_task = asyncio.create_task(self._position_loop())

        # 启动摆轮机触发监控循环
        self._divert_monitor_task = asyncio.create_task(self._divert_monitor_loop())

        self.logger.info(f"ArchiveService 启动完成")

    async def stop(self) -> None:
        """停止跟踪服务"""
        if not self._running:
            return

        self._running = False
        self.logger.info("ArchiveService 停止中...")

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
        await self._disconnect_plc()

        self.logger.info("ArchiveService 已停止")

    # =============================
    # PLC 连接管理
    # =============================

    async def _connect_plc(self) -> None:
        """连接 PLC"""
        try:
            self._plc_client = AsyncModbusTcpClient(
                host=self.plc_ip,
                port=self.plc_port,
                timeout=3.0
            )
            connected = await self._plc_client.connect()
            if connected:
                self._plc_connected = True
                self.logger.info(f"PLC 连接成功: {self.plc_ip}:{self.plc_port}")
            else:
                self.logger.error(f"PLC 连接失败: {self.plc_ip}:{self.plc_port}")
        except Exception as e:
            self.logger.error(f"PLC 连接异常: {e}")

    async def _disconnect_plc(self) -> None:
        """断开 PLC 连接"""
        if self._plc_client:
            self._plc_client.close()
            self._plc_connected = False
            self.logger.info("PLC 已断开")

    async def _write_plc_register(self, addr: int, value: int) -> bool:
        """
        写入 PLC 寄存器

        Args:
            addr: 寄存器地址
            value: 写入值

        Returns:
            是否成功
        """
        if not self._plc_connected or not self._plc_client:
            self.logger.warning(f"PLC 未连接，无法写入寄存器 {addr}={value}")
            return False

        try:
            result = await self._plc_client.write_register(addr, value, slave=1)
            if result.isError():
                self.logger.error(f"写入寄存器失败: addr={addr}, value={value}")
                return False
            self.logger.debug(f"PLC 写入成功: D{addr}={value}")
            return True
        except Exception as e:
            self.logger.error(f"PLC 写入异常: {e}")
            return False

    # =============================
    # 核心控制逻辑（根据真实接口）
    # =============================

    async def _send_plc_signal(self, direction: int) -> bool:
        """
        发送 PLC 转向信号（根据真实接口逻辑）

        物理布局：
        - 方向1: 只过摆轮机1 → 只触发 D0
        - 方向2/3/4: 先过摆轮机1，再过摆轮机2 → 需要触发 D0 和 D1

        Args:
            direction: 方向编号 (1-4)

        Returns:
            是否成功
        """
        self.logger.info(f"📡 [PLC] 发送转向信号: 方向 {direction}")

        if direction == 1:
            # 方向1: 只触发摆轮机1 (D0)
            await asyncio.sleep(self.T_D0)
            success = await self._write_plc_register(self.D0_ADDR, direction)
            if success:
                self._stats["plc_sent"] += 1
                self.logger.info(f"✅ [PLC] 方向1: D0={direction} (摆轮机1转向)")
            return success

        else:
            # 方向2/3/4: 需要同时触发摆轮机1和摆轮机2
            # 先触发 D0（摆轮机1）
            await asyncio.sleep(self.T_D0)
            success1 = await self._write_plc_register(self.D0_ADDR, direction)

            if success1:
                self.logger.info(f"✅ [PLC] 方向{direction}: D0={direction} (摆轮机1转向)")

            # 等待到摆轮机2的时间，再触发 D1
            await asyncio.sleep(self.T_D1 - self.T_D0)
            success2 = await self._write_plc_register(self.D1_ADDR, direction)

            if success2:
                self.logger.info(f"✅ [PLC] 方向{direction}: D1={direction} (摆轮机2转向)")
                self._stats["plc_sent"] += 1

            return success1 and success2

    # =============================
    # 位置推算循环
    # =============================

    async def _position_loop(self) -> None:
        """
        位置推算主循环

        定期更新所有活动鞋盒的位置
        """
        interval = get_config("archive_service.update_interval_ms", 20) / 1000.0

        self.logger.info(f"位置推算循环启动，更新间隔={interval * 1000:.1f}ms")

        while self._running:
            try:
                now_ms = time.time_ns() / 1_000_000
                for box in list(self._active_boxes.values()):
                    if box.has_exited:
                        continue

                    elapsed_ms = now_ms - box.last_update_ms
                    if elapsed_ms <= 0:
                        continue

                    elapsed_s = elapsed_ms / 1000.0
                    delta_pos = box.speed_mm_s * elapsed_s
                    box.current_pos_mm += delta_pos
                    box.last_update_ms = now_ms

                    if box.current_pos_mm >= self.max_pos_mm:
                        box.has_exited = True
                        box.status = TrackStatus.FINALIZED
                        self._stats["exited_count"] += 1

                self._stats["active_count"] = len(self._active_boxes)
                self._stats["finished_count"] = len(self._finished_boxes)

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"位置推算循环异常: {e}", exc_info=True)
                await asyncio.sleep(0.1)

        self.logger.info("位置推算循环结束")

    # =============================
    # 摆轮机触发监控循环
    # =============================

    async def _divert_monitor_loop(self) -> None:
        """
        摆轮机触发监控循环

        检查每个鞋盒是否到达摆轮机触发位置，如果是则发送 PLC 信号

        注意：这里使用的是基于位置的触发，但真实接口是基于时间的（T_D0/T_D1）
        由于我们有位置推算，可以将距离转换为时间，或者直接使用时间延迟
        """
        interval = 0.02  # 20ms 检查一次

        self.logger.info(f"摆轮机监控循环启动")

        while self._running:
            try:
                for box in list(self._active_boxes.values()):
                    # 只处理已分配路径且未触发的鞋盒
                    if box.status != TrackStatus.WAITING_DIVERT:
                        continue

                    if box.target_divert_id is None:
                        continue

                    if box.plc_triggered:
                        continue

                    # 计算当前位置
                    now_ms = time.time_ns() / 1_000_000
                    elapsed_s = (now_ms - box.last_update_ms) / 1000.0
                    current_pos = box.current_pos_mm + (box.speed_mm_s * elapsed_s)

                    # 获取目标摆轮机位置
                    divert_pos = self.divert_position_map.get(box.target_divert_id, float('inf'))

                    # 到达摆轮机位置时触发（而不是提前500mm）
                    if current_pos >= divert_pos:
                        direction = box.path_id

                        self.logger.info(f"🔔 [触发] 鞋盒 {box.track_id} 到达摆轮机 {box.target_divert_id}, "
                                         f"方向={direction}, 位置={current_pos:.1f}mm")

                        # 发送 PLC 信号
                        success = await self._send_plc_signal(direction)

                        if success:
                            box.plc_triggered = True
                            box.status = TrackStatus.DIVERT_TRIGGERED
                            self._stats["divert_triggered"] += 1

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
        """处理 PE1 上升沿事件（鞋盒进入入口）"""
        now_ms = time.time_ns() / 1_000_000

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
        """处理 PE2 上升沿事件（鞋盒到达出口）"""
        now_ms = time.time_ns() / 1_000_000

        box = self._active_boxes.get(track.track_id)
        if not box:
            self.logger.warning(f"[PE2] 未找到鞋盒跟踪: {track.track_id}")
            return

        box.current_pos_mm = self.pe2_pos_mm
        box.last_update_ms = now_ms
        box.pe2_on_ms = track.pe2_on_ms or now_ms

        if track.speed_mm_s and track.speed_mm_s > 0:
            box.speed_mm_s = track.speed_mm_s

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
            分配的路径ID，失败返回 None
        """
        box = self._active_boxes.get(track_id)
        if not box:
            self.logger.warning(f"扫码结果: 未找到鞋盒 {track_id}")
            return None

        path_id = _code_to_path(code)
        if path_id is None:
            self.logger.warning(f"码值 {code} 无法映射到有效路径")
            return None

        path = self.paths.get(path_id)
        if not path:
            self.logger.error(f"路径 {path_id} 不存在")
            return None

        target_divert_id = _get_divert_for_path(path)
        if target_divert_id is None:
            self.logger.warning(f"路径 {path_id} 没有配置摆轮机")
            return None

        box.path_id = path_id
        box.path_config = path
        box.target_divert_id = target_divert_id
        box.status = TrackStatus.WAITING_DIVERT

        self.logger.info(f"[扫码] 鞋盒 {track_id} 规划完成: "
                         f"码值={code}, 路径={path_id}, 摆轮机={target_divert_id}")

        return path_id

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

    def get_queue_status(self) -> dict:
        """获取队列状态信息"""
        now_ms = time.time_ns() / 1_000_000

        queue_items = []
        for box in self._active_boxes.values():
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

        queue_items.sort(key=lambda x: x["position"])

        return {
            "active_count": len(self._active_boxes),
            "finished_count": len(self._finished_boxes),
            "queue": queue_items,
            "head_box": queue_items[-1] if queue_items else None,
            "tail_box": queue_items[0] if queue_items else None,
            "timestamp": now_ms
        }

    def get_position(self, track_id: str) -> Optional[BoxTrackingData]:
        """获取鞋盒当前位置"""
        box = self._active_boxes.get(track_id)
        if box:
            return box

        for box in self._finished_boxes:
            if box.track_id == track_id:
                return box

        return None

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            **self._stats,
            "max_pos_mm": self.max_pos_mm,
            "pe1_pos_mm": self.pe1_pos_mm,
            "pe2_pos_mm": self.pe2_pos_mm,
            "camera_pos_mm": self.camera_pos_mm,
            "plc_connected": self._plc_connected,
            "t_d0": self.T_D0,
            "t_d1": self.T_D1
        }