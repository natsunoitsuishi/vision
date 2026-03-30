import asyncio
import time
from dataclasses import dataclass
from typing import Dict, Optional

from config import get_config
from infra import get_logger


@dataclass
class BoxPosition:
    track_id: str
    current_pos: float = 0.0  # 当前位置 mm
    speed: float = 500.0  # 传送带速度 mm/s
    last_update: float = 0.0
    has_exited: bool = False


class ArchiveService:
    def __init__(self):
        # 传送带布局（毫米）
        self.PE1_POS = 0.0  # 入口光电
        self.PE2_POS = self.PE1_POS + get_config("pe1_to_pe2_dist")
        self.MAX_POS = 1500.0  # 最大位置（超出即离开）

        self.active_boxes: Dict[str, BoxPosition] = {}
        self._running = False
        self.logger = get_logger(__name__)

    async def start(self):
        """启动位置自动推算"""
        self.logger.info(f"启动位置自动推算 ...")
        self._running = True
        asyncio.create_task(self._auto_update_loop())

    async def stop(self):
        self._running = False

    # ==================== 核心：自动位置推算 ====================
    async def _auto_update_loop(self):
        while self._running:
            now = time.time_ns() / 1_000_000
            for tid, box in list(self.active_boxes.items()):
                if box.has_exited:
                    continue

                # 时间差
                elapsed = now - box.last_update
                if elapsed <= 0:
                    continue

                # 核心公式：位置 = 原位置 + 速度 × 时间
                box.current_pos += box.speed * elapsed
                box.last_update = now

                # 超出传送带 → 标记离开
                if box.current_pos >= self.MAX_POS:
                    box.has_exited = True

            await asyncio.sleep(0.05)  # 50ms 更新一次

    # ==================== 外部事件触发（光电/相机） ====================
    def handle_on_pe1(self, track_id: str):
        """鞋盒触发入口光电 → 创建新轨迹"""
        now = time.time_ns() / 1_000_000
        box = BoxPosition(
            track_id=track_id,
            current_pos=self.PE1_POS,
            last_update=now
        )
        self.active_boxes[track_id] = box

    def handle_on_pe2(self, track_id: str, box_speed: float):
        """触发出口光电 → 强制校准位置"""
        box = self.active_boxes.get(track_id)
        if box:
            box.current_pos = self.PE2_POS
            box.last_update = time.time_ns() / 1_000_000
            box.speed = box_speed

    # ==================== 查询接口 ====================
    def get_position(self, track_id: str) -> Optional[dict]:
        box = self.active_boxes.get(track_id)
        if not box:
            return None
        return {
            "track_id": box.track_id,
            "current_pos_mm": round(box.current_pos, 1),
            "speed_mm_s": box.speed,
            "has_exited": box.has_exited
        }