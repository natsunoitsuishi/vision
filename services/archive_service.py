import asyncio
import time
from dataclasses import dataclass
from typing import Dict, Optional
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import threading

from config import get_config
from infra import get_logger


@dataclass
class BoxTrack:
    id: str
    speed_mm_s: float = 500.0

@dataclass
class BoxPosition:
    track: BoxTrack
    current_pos: float = 0.0
    speed: float = 500.0
    last_update: float = 0.0
    has_exited: bool = False

# ==================== 核心服务 ====================
class ArchiveService:
    def __init__(self):
        self.PE1_POS = 0.0
        self.PE2_POS = 1200
        self.MAX_POS = 1500.0

        self.active_boxes: Dict[str, BoxPosition] = {}
        self._running = False
        self.logger = get_logger(__name__)

    # ==================== 你要的唯一入口 ====================
    async def start(self):
        self.logger.info("启动位置推算 + HTTP 服务")
        self._running = True

        # 1. 启动位置推算（异步）
        asyncio.create_task(self._position_loop())

        # 2. 启动 HTTP 服务（线程启动，不阻塞）
        self._start_http_server()

    # ==================== 纯 Python HTTP 服务（不用 Flask、不用 uvicorn） ====================
    def _start_http_server(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(handler_self):
                try:
                    if handler_self.path == "/":
                        self._get_all(handler_self)
                    elif handler_self.path.startswith("/position/"):
                        self._get_position(handler_self)
                    else:
                        handler_self.send_response(404)
                        handler_self.end_headers()
                except:
                    pass

            def log_message(self, fmt, *args):
                pass  # 关闭日志

        server = HTTPServer(("127.0.0.1", 5000), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

    # ==================== API 接口 ====================
    def _get_all(self, handler):
        res = []
        for t, b in self.active_boxes.items():
            res.append({
                "track_id": t.id,
                "pos": round(b.current_pos, 1),
                "speed": b.speed,
                "exited": b.has_exited
            })
        data = json.dumps({"count": len(res), "boxes": res}).encode()

        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.end_headers()
        handler.wfile.write(data)

    def _get_position(self, handler):
        track_id = handler.path.split("/position/")[-1]
        for t, b in self.active_boxes.items():
            if t.id == track_id:
                data = json.dumps({
                    "track_id": t.id,
                    "pos": round(b.current_pos, 1)
                }).encode()
                handler.send_response(200)
                handler.send_header("Content-Type", "application/json")
                handler.end_headers()
                handler.wfile.write(data)
                return

        handler.send_response(404)
        handler.end_headers()

    # ==================== 位置推算 ====================
    async def _position_loop(self):
        while self._running:
            now = time.time() * 1000
            for track, box in list(self.active_boxes.items()):
                if box.has_exited:
                    continue

                elapsed = now - box.last_update
                if elapsed <= 0:
                    continue

                box.current_pos += box.speed * (elapsed / 1000)
                box.last_update = now

                if box.current_pos >= self.MAX_POS:
                    box.has_exited = True

            await asyncio.sleep(0.05)

    # ==================== 光电触发 ====================
    def handle_on_pe1(self, track: BoxTrack):
        now = time.time() * 1000
        box = BoxPosition(
            track=track,
            current_pos=self.PE1_POS,
            last_update=now,
            speed=track.speed_mm_s
        )
        self.active_boxes[track.id] = box

    def handle_on_pe2(self, track: BoxTrack):
        box = self.active_boxes.get(track.id)
        if box:
            box.current_pos = self.PE2_POS
            box.last_update = time.time() * 1000
            box.speed = track.speed_mm_s