# services/archive_service.py
"""
鞋盒位置推算服务

根据光电传感器触发时间和传送带速度，实时推算每个鞋盒的当前位置。
支持 HTTP 查询接口，供外部系统获取鞋盒位置。
"""
import asyncio
import time
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Dict, Optional, List
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

from domain.models import BoxTrack
from config.manager import get_config


@dataclass
class BoxPosition:
    """
    鞋盒位置推算数据

    Attributes:
        track_id: 轨迹ID
        current_pos_mm: 当前位置（毫米），以 PE1 为原点
        speed_mm_s: 当前速度（毫米/秒）
        last_update_ms: 最后更新时间戳（毫秒）
        has_exited: 是否已离开传送带
        created_ms: 创建时间
        pe1_on_ms: PE1 触发时间
        pe2_on_ms: PE2 触发时间
        length_mm: 鞋盒长度（毫米，估算值）
        last_known_pos_mm: 最后已知位置（用于推算）
    """
    track_id: str
    current_pos_mm: float = 0.0
    speed_mm_s: float = 0.0
    last_update_ms: float = 0.0
    has_exited: bool = False
    created_ms: float = 0.0
    pe1_on_ms: float = 0.0
    pe2_on_ms: float = 0.0
    length_mm: float = 0.0
    last_known_pos_mm: float = 0.0

    def to_dict(self) -> dict:
        """转换为字典格式"""
        return {
            "track_id": self.track_id,
            "current_pos_mm": round(self.current_pos_mm, 1),
            "speed_mm_s": round(self.speed_mm_s, 1),
            "has_exited": self.has_exited,
            "created_ms": self.created_ms,
            "pe1_on_ms": self.pe1_on_ms,
            "pe2_on_ms": self.pe2_on_ms,
            "length_mm": round(self.length_mm, 1),
            "last_update_ms": self.last_update_ms
        }


class ArchiveService:
    """
    鞋盒位置推算服务

    职责：
    1. 根据 PE1/PE2 触发事件创建/更新鞋盒轨迹
    2. 基于传送带速度实时推算每个鞋盒的当前位置
    3. 提供 HTTP 接口供外部系统查询鞋盒位置
    4. 当鞋盒离开传送带后自动清理

    物理模型：
        PE1 (入口) 位置: 0 mm
        PE2 (出口) 位置: PE1_TO_PE2_DIST_MM
        相机位置: PE2_TO_CAMERA_DIST_MM (相对于 PE2)

    位置计算：
        pos = last_known_pos + speed * (current_time - last_update_time)
    """

    def __init__(self):
        """初始化位置推算服务"""
        self.logger = logging.getLogger(__name__)

        # 物理参数（从配置读取）
        self.pe1_pos_mm = 0.0  # PE1 位置（原点）
        self.pe2_pos_mm = get_config("pe1_to_pe2_dist", 0.39) * 1000  # PE2 位置（毫米）
        self.camera_pos_mm = self.pe2_pos_mm + get_config("pe2_to_camera_dist", 0.36) * 1000  # 相机位置

        # 传送带参数
        self.conveyor_speed_mm_s = get_config("conveyor.default_speed_mm_s", 500.0)
        self.max_pos_mm = self.camera_pos_mm + 500  # 最大跟踪位置（相机后方 500mm）

        # 活动鞋盒字典
        self._active_boxes: Dict[str, BoxPosition] = {}

        # 已完成的鞋盒（用于查询历史，最多保留 1000 个）
        self._finished_boxes: List[BoxPosition] = []
        self._max_finished = 1000

        # 运行状态
        self._running = False
        self._position_task: Optional[asyncio.Task] = None

        # HTTP 服务器配置
        self._http_port = get_config("archive_service.http_port", 5000)
        self._http_host = get_config("archive_service.http_host", "127.0.0.1")
        self._http_server: Optional[HTTPServer] = None
        self._http_thread: Optional[threading.Thread] = None

        # 统计信息
        self._stats = {
            "total_boxes": 0,
            "active_count": 0,
            "finished_count": 0,
            "exited_count": 0
        }

    # =============================
    # 生命周期管理
    # =============================

    async def start(self) -> None:
        """启动位置推算服务和 HTTP API"""
        if self._running:
            self.logger.warning("ArchiveService 已经在运行")
            return

        self._running = True
        self.logger.info(f"ArchiveService 启动，物理参数: PE1={self.pe1_pos_mm}mm, "
                         f"PE2={self.pe2_pos_mm}mm, 相机={self.camera_pos_mm}mm")

        # 启动位置推算循环
        self._position_task = asyncio.create_task(self._position_loop())

        # 启动 HTTP 服务器（在独立线程中运行）
        self._start_http_server()

        self.logger.info(f"ArchiveService 启动完成，HTTP API: http://{self._http_host}:{self._http_port}")

    async def stop(self) -> None:
        """停止位置推算服务"""
        if not self._running:
            return

        self._running = False
        self.logger.info("ArchiveService 停止中...")

        # 停止位置推算循环
        if self._position_task and not self._position_task.done():
            self._position_task.cancel()
            try:
                await self._position_task
            except asyncio.CancelledError:
                pass

        # 停止 HTTP 服务器
        self._stop_http_server()

        self.logger.info("ArchiveService 已停止")

    # =============================
    # 位置推算循环
    # =============================

    async def _position_loop(self) -> None:
        """
        位置推算主循环

        定期更新所有活动鞋盒的位置，并检查是否已离开传送带
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
                        self._stats["exited_count"] += 1
                        self.logger.debug(f"鞋盒 {box.track_id} 已离开传送带，"
                                          f"最后位置={box.current_pos_mm:.1f}mm")

                        # 移动到已完成列表
                        self._active_boxes.pop(box.track_id, None)
                        self._finished_boxes.append(box)

                        # 清理过多的已完成记录
                        if len(self._finished_boxes) > self._max_finished:
                            self._finished_boxes = self._finished_boxes[-self._max_finished:]

                # 更新统计
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
    # 光电事件处理
    # =============================

    def handle_on_pe1(self, track: BoxTrack) -> None:
        """
        处理 PE1 上升沿事件（鞋盒进入入口）

        Args:
            track: 鞋盒轨迹对象
        """
        now_ms = time.time_ns() / 1_000_000

        # 创建位置推算对象
        box = BoxPosition(
            track_id=track.track_id,
            current_pos_mm=self.pe1_pos_mm,
            speed_mm_s=track.speed_mm_s or self.conveyor_speed_mm_s,
            last_update_ms=now_ms,
            has_exited=False,
            created_ms=now_ms,
            pe1_on_ms=track.pe1_on_ms or now_ms,
            pe2_on_ms=track.pe2_on_ms or 0,
            length_mm=track.length_mm or 0,
            last_known_pos_mm=self.pe1_pos_mm
        )

        self._active_boxes[track.track_id] = box
        self._stats["total_boxes"] += 1

        self.logger.info(f"[PE1] 创建鞋盒位置跟踪: {track.track_id}, "
                         f"初始速度={box.speed_mm_s:.1f}mm/s")

    def handle_on_pe2(self, track: BoxTrack) -> None:
        """
        处理 PE2 上升沿事件（鞋盒到达出口）

        在 PE2 触发时，我们获取更精确的速度和位置信息

        Args:
            track: 鞋盒轨迹对象
        """
        now_ms = time.time_ns() / 1_000_000

        box = self._active_boxes.get(track.track_id)
        if not box:
            self.logger.warning(f"[PE2] 未找到鞋盒位置记录: {track.track_id}")
            return

        # 更新位置为 PE2
        box.current_pos_mm = self.pe2_pos_mm
        box.last_update_ms = now_ms
        box.pe2_on_ms = track.pe2_on_ms or now_ms

        # 如果有更精确的速度，更新速度
        if track.speed_mm_s and track.speed_mm_s > 0:
            box.speed_mm_s = track.speed_mm_s

        # 估算鞋盒长度（PE1 到 PE2 的时间差 * 速度）
        if box.pe1_on_ms > 0 and box.pe2_on_ms > 0:
            time_diff_s = (box.pe2_on_ms - box.pe1_on_ms) / 1000.0
            if time_diff_s > 0:
                box.length_mm = box.speed_mm_s * time_diff_s
                self.logger.debug(f"[PE2] 估算鞋盒长度: {box.track_id} -> {box.length_mm:.1f}mm")

        self.logger.info(f"[PE2] 更新鞋盒位置: {track.track_id}, "
                         f"位置={box.current_pos_mm:.1f}mm, 速度={box.speed_mm_s:.1f}mm/s")

    def handle_speed_update(self, track_id: str, speed_mm_s: float) -> None:
        """
        更新鞋盒速度（当有更精确的速度测量时）

        Args:
            track_id: 轨迹ID
            speed_mm_s: 新速度（毫米/秒）
        """
        box = self._active_boxes.get(track_id)
        if box:
            old_speed = box.speed_mm_s
            box.speed_mm_s = speed_mm_s
            self.logger.debug(f"更新鞋盒速度: {track_id}, {old_speed:.1f} -> {speed_mm_s:.1f}mm/s")

    # =============================
    # 位置查询接口
    # =============================

    def get_position(self, track_id: str) -> Optional[BoxPosition]:
        """
        获取鞋盒当前位置

        Args:
            track_id: 轨迹ID

        Returns:
            鞋盒位置信息，不存在则返回 None
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
        # 在返回前更新一次位置（计算最新位置）
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
                "has_exited": False,
                "estimated_time_to_camera_ms": max(0, (self.camera_pos_mm - current_pos) / box.speed_mm_s * 1000)
                if box.speed_mm_s > 0 else -1,
                "length_mm": round(box.length_mm, 1)
            })

        return result

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            **self._stats,
            "max_pos_mm": self.max_pos_mm,
            "pe1_pos_mm": self.pe1_pos_mm,
            "pe2_pos_mm": self.pe2_pos_mm,
            "camera_pos_mm": self.camera_pos_mm,
            "conveyor_speed_mm_s": self.conveyor_speed_mm_s,
            "update_interval_ms": get_config("archive_service.update_interval_ms", 20)
        }

    # =============================
    # HTTP API 服务
    # =============================

    def _start_http_server(self) -> None:
        """启动 HTTP 服务器（在独立线程中）"""

        class Handler(BaseHTTPRequestHandler):
            """HTTP 请求处理器"""

            def log_message(self, fmt, *args):
                """禁用默认日志"""
                pass

            def do_GET(self):
                """处理 GET 请求"""
                try:
                    if self.path == "/":
                        self._handle_root()
                    elif self.path == "/api/positions":
                        self._handle_all_positions()
                    elif self.path.startswith("/api/position/"):
                        self._handle_single_position()
                    elif self.path == "/api/stats":
                        self._handle_stats()
                    else:
                        self.send_response(404)
                        self.end_headers()
                        self.wfile.write(b'{"error": "Not found"}')
                except Exception as e:
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(f'{{"error": "{str(e)}"}}'.encode())

            def _handle_root(self):
                """根路径，返回服务信息"""
                data = {
                    "service": "ArchiveService - 鞋盒位置推算服务",
                    "endpoints": {
                        "/api/positions": "获取所有活动鞋盒位置",
                        "/api/position/{track_id}": "获取指定鞋盒位置",
                        "/api/stats": "获取服务统计信息"
                    },
                    "config": {
                        "pe1_pos_mm": self.server.archive_service.pe1_pos_mm,
                        "pe2_pos_mm": self.server.archive_service.pe2_pos_mm,
                        "camera_pos_mm": self.server.archive_service.camera_pos_mm
                    }
                }
                self._send_json(data)

            def _handle_all_positions(self):
                """获取所有活动鞋盒位置"""
                positions = self.server.archive_service.get_all_active_positions()
                self._send_json({
                    "count": len(positions),
                    "timestamp_ms": time.time_ns() / 1_000_000,
                    "positions": positions
                })

            def _handle_single_position(self):
                """获取单个鞋盒位置"""
                # 解析 track_id
                parts = self.path.split("/")
                if len(parts) < 4:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b'{"error": "Missing track_id"}')
                    return

                track_id = parts[3]
                box = self.server.archive_service.get_position(track_id)

                if box:
                    self._send_json(box.to_dict())
                else:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(f'{{"error": "Track not found: {track_id}"}}'.encode())

            def _handle_stats(self):
                """获取统计信息"""
                stats = self.server.archive_service.get_stats()
                stats["timestamp_ms"] = time.time_ns() / 1_000_000
                self._send_json(stats)

            def _send_json(self, data):
                """发送 JSON 响应"""
                response = json.dumps(data, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", len(response))
                self.end_headers()
                self.wfile.write(response)

        # 创建服务器实例
        self._http_server = HTTPServer((self._http_host, self._http_port), Handler)
        # 将服务实例绑定到服务器，供 Handler 访问
        self._http_server.archive_service = self

        # 在独立线程中启动服务器
        self._http_thread = threading.Thread(
            target=self._http_server.serve_forever,
            daemon=True,
            name="ArchiveService-HTTP"
        )
        self._http_thread.start()

        self.logger.info(f"HTTP API 服务器启动: http://{self._http_host}:{self._http_port}")

    def _stop_http_server(self) -> None:
        """停止 HTTP 服务器"""
        if self._http_server:
            self._http_server.shutdown()
            self._http_server = None

        if self._http_thread:
            self._http_thread.join(timeout=2.0)
            self._http_thread = None

        self.logger.info("HTTP API 服务器已停止")

    # =============================
    # 清理方法
    # =============================

    def clear_finished(self) -> None:
        """清理所有已完成的鞋盒记录"""
        count = len(self._finished_boxes)
        self._finished_boxes.clear()
        self.logger.info(f"清理了 {count} 个已完成鞋盒记录")

    def reset(self) -> None:
        """重置所有状态"""
        self._active_boxes.clear()
        self._finished_boxes.clear()
        self._stats = {
            "total_boxes": 0,
            "active_count": 0,
            "finished_count": 0,
            "exited_count": 0
        }
        self.logger.info("ArchiveService 已重置")


# =============================
# 便捷函数
# =============================

def create_archive_service() -> ArchiveService:
    """
    创建 ArchiveService 实例的便捷函数

    Returns:
        ArchiveService 实例
    """
    return ArchiveService()