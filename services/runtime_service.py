# services/runtime_service.py
"""
运行时服务 - 核心业务编排服务
"""
import asyncio
from infra import get_logger
import time
from typing import Optional, Dict
from datetime import datetime

from config.manager import get_config
from devices import MesClient, SchedulerClient
from devices.camera import OptCameraClient
from devices.photoelectric import PhotoelectricClient
from domain.binder import ResultBinder
from domain.decision_engine import DecisionEngine
from domain.enums import EventType, DecisionStatus, RunMode
from domain.models import BoxTrack, CameraResult, AppEvent
from domain.scan_session import ScanSessionController
from domain.scheduler import TriggerScheduler
from domain.track_manager import TrackManager
from infra import get_logger
from infra.db.repository import SQLiteRepository
from services import ArchiveService
from services.event_bus import EventBus


class RuntimeService:
    """
    运行时服务 - 统一协调所有业务模块

    职责：
    1. 统一协调 DI、相机、调度器、绑定器和判定引擎
    2. 消费系统事件（从 EventBus 获取）
    3. 推进 BoxTrack 生命周期
    4. 处理业务异常和报警
    """

    def __init__(
            self,
            event_bus: EventBus,
            track_manager: TrackManager,
            trigger_scheduler: TriggerScheduler,
            scan_session_controller: ScanSessionController,
            result_binder: ResultBinder,
            decision_engine: DecisionEngine,
            photoelectric_client: PhotoelectricClient,
            cameras: Dict[str, OptCameraClient],
            repository: SQLiteRepository,
            scheduler_client: SchedulerClient,
            mes_client: MesClient,
            archive_service: ArchiveService,
    ):
        """
        初始化运行时服务

        Args:
            event_bus: 事件总线
            track_manager: 轨迹管理器
            trigger_scheduler: 触发器调度器
            scan_session_controller: 扫码会话控制器
            result_binder: 结果绑定器
            decision_engine: 决策引擎
            photoelectric_client: DI/O 服务
            cameras: 相机客户端字典
            repository: 数据仓储
        """

        self.event_bus = event_bus
        self.track_manager = track_manager
        self.trigger_scheduler = trigger_scheduler
        self.scan_session_controller = scan_session_controller
        self.result_binder = result_binder
        self.decision_engine = decision_engine
        self.photoelectric_client = photoelectric_client
        self.cameras = cameras
        self.repository = repository
        self.scheduler_client = scheduler_client
        self.mes_client = mes_client
        self.archive_service = archive_service

        # 运行时状态
        self._running = False
        self._event_loop_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._current_mode = RunMode.LR

        # 日志
        self.logger = get_logger(__name__)

        # 统计
        self.stats = {
            "total_tracks": 0,
            "ok_count": 0,
            "ng_count": 0,
            "ambiguous_count": 0,
            "timeout_count": 0,
            "fault_count": 0
        }


    # =============================
    # 生命周期管理
    # =============================

    async def start(self) -> None:
        """启动运行时服务"""
        if self._running:
            self.logger.warning("RuntimeService 已经在运行")
            return

        self._running = True
        self.logger.info("RuntimeService 启动")

        # 订阅事件（关键修改！）
        self.event_bus.subscribe(EventType.PE_RISE, self._on_pe_rise)
        self.event_bus.subscribe(EventType.PE_FALL, self._on_pe_fall)
        self.event_bus.subscribe(EventType.CAMERA_RESULT, self._on_camera_result)
        # self.event_bus.subscribe(EventType.CAMERA_HEARTBEAT, self._on_camera_heartbeat)
        self.event_bus.subscribe(EventType.TRACK_TIMEOUT, self._on_track_timeout)
        self.event_bus.subscribe(EventType.DEVICE_FAULT, self._on_device_fault)

        # 启动超时清理任务
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

        # 启动 DI 监听
        if self.photoelectric_client and not self.photoelectric_client.is_running:
            await self.photoelectric_client.start_monitoring()

        # 启动相机接收循环
        for camera_id, camera in self.cameras.items():
            if not camera.is_scanning:
                await camera.start_scan_session()

        self.logger.info("RuntimeService 启动完成")

    async def stop(self) -> None:
        """停止运行时服务"""
        if not self._running:
            return

        self._running = False
        self.logger.info("RuntimeService 停止中...")

        # 取消订阅
        self.event_bus.unsubscribe(EventType.PE_RISE, self._on_pe_rise)
        self.event_bus.unsubscribe(EventType.PE_FALL, self._on_pe_fall)
        self.event_bus.unsubscribe(EventType.CAMERA_RESULT, self._on_camera_result)
        self.event_bus.unsubscribe(EventType.CAMERA_HEARTBEAT, self._on_camera_heartbeat)
        self.event_bus.unsubscribe(EventType.TRACK_TIMEOUT, self._on_track_timeout)
        self.event_bus.unsubscribe(EventType.DEVICE_FAULT, self._on_device_fault)

        # 取消清理任务
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # 停止相机扫描
        for camera in self.cameras.values():
            await camera.stop_scan_session()

        # 停止 DI 监听
        if self.photoelectric_client:
            await self.photoelectric_client.stop_monitoring()

        self.logger.info("RuntimeService 已停止")

    async def _on_pe_rise(self, event: AppEvent) -> None:
        """
        处理 PE 上升沿事件

        PE1 上升沿：创建新轨迹
        PE2 上升沿：匹配轨迹并打开扫描窗口
        """
        sensor = event.payload.get("sensor")
        timestamp = event.payload.get("timestamp")

        self.logger.info(f"[_on_pe_rise] 收到事件负载, "
                         f"sensor: {event.payload.get('sensor')}" 
                         f"channel: {event.payload.get('channel')}" 
                         f"state: {event.payload.get('state')}" 
                         f"previous_state: {event.payload.get('previous_state')}" 
                         f"timestamp: {event.payload.get('timestamp')}"
                         )

        if sensor == "PE1":
            # 创建新轨迹
            track = self.track_manager.create_track(timestamp, self._current_mode)
            self.stats["total_tracks"] += 1

            self.logger.info(f"[PE1] 创建轨迹: {track.track_id}, "
                             f"活动轨迹数={self.track_manager.active_count}")

            # 通知 UI 更新
            await self._notify_ui_track_created(track)

        elif sensor == "PE2":
            # 匹配轨迹
            track = self.track_manager.match_track_for_pe2(timestamp)

            if track is None:
                self.logger.warning("[PE2] 没有匹配的轨迹")
                await self._raise_alarm("PE2_NO_MATCH", "PE2 触发但没有匹配的轨迹")
                return

            # 计算速度
            if track.pe1_on_ms is not None:
                time_diff_ms = timestamp - track.pe1_on_ms
                sensor_distance = get_config("pe1_to_pe2_dist")
                if time_diff_ms > 0:
                    # 配置中是米，需要转换为毫米
                    pe1_to_pe2_dist_m = get_config("pe1_to_pe2_dist")
                    pe1_to_pe2_dist_mm = pe1_to_pe2_dist_m * 1000  # 390 mm
                    time_diff_sec = time_diff_ms / 1000  # 转换为秒
                    track.speed_mm_s = pe1_to_pe2_dist_mm / time_diff_sec
                    self.logger.info(f"[PE2] 速度={track.speed_mm_s:.10f}mm/s")
                else:
                    self.logger.error(f"time_diff_ms <= 0")
                    # track.speed_mm_s = get_config("conveyor.default_speed_mm_s", 800)
            else:
                self.logger.error(f"track.pe1_on_ms is None")
                # track.speed_mm_s = get_config("conveyor.default_speed_mm_s", 800)

            # 打开扫描窗口
            self.trigger_scheduler.open_scan_window(track, track.speed_mm_s, track.pe2_on_ms)
            # 1774779341319.7307, pe2_on_ms:
            # 1774779340715.5332
            # 1774779345127,
            # 1774779341269.7307,
            # 1774779341369.7307
            # 确保扫码会话运行
            await self.scan_session_controller.ensure_running()

            self.logger.info(f"[PE2] 匹配轨迹: {track.track_id}, "
                             f"速度={track.speed_mm_s:.10f}mm/s, "
                             f"窗口={track.scan_window_start_ms}~{track.scan_window_end_ms}")

            # 通知 UI 更新
            await self._notify_ui_window_opened(track)

    async def _on_pe_fall(self, event: AppEvent) -> None:
        """
        处理 PE 下降沿事件

        PE1 下降沿：准备关闭窗口
        """
        sensor = event.payload.get("sensor")
        ts = event.payload.get("ts", time.time())

        if sensor == "PE1":
            track = self.track_manager.match_last_open_track()
            if track:
                track.pe1_off_ts = ts
                self.logger.info(f"[PE1下降] 准备关闭轨迹窗口: {track.track_id}")
            else:
                self.logger.debug("[PE1下降] 没有打开的轨迹窗口")

    async def _on_camera_result(self, event: AppEvent) -> None:
        """
            处理相机结果事件
        """
        payload = event.payload
        if payload.get("result") == "TRUE":
            self.logger.info(f"[_on_camera_result] 收到事件负载, "
                             f"camera_id: {event.payload.get('camera_id')} " 
                             f"result: {event.payload.get('result')} " 
                             f"code: {event.payload.get('code')} " 
                             f"symbology: {event.payload.get('symbology')} " 
                             f"ts_ms: {event.payload.get('ts_ms')} "
                             )


            # 获取活动轨迹
            active_tracks = self.track_manager.get_active_tracks()
            if not active_tracks:
                self.logger.warning(f"[相机{payload.get('camera_id')}] 收到结果但没有活动轨迹")
                await self._raise_alarm("NO_ACTIVE_TRACK", "收到扫码结果但没有活动轨迹")
                return
            # 构建 CameraResult 对象
            camera_result = CameraResult(
                camera_id=payload.get("camera_id"),
                code=payload.get("code"),
                ts_ms=payload.get("ts_ms"),
                result="TRUE" if payload.get("result") == "TRUE" else "FALSE",
                symbology=payload.get("symbology"),
                raw_payload=payload,
            )

            # 绑定结果到轨迹
            track = self.result_binder.bind(camera_result, active_tracks)

            if track is None:
                self.logger.error(f"[相机{payload.get('camera_id')}] 结果无法绑定到任何轨迹")
                await self._raise_alarm("UNBOUND_RESULT", f"扫码结果无法绑定: {camera_result.code}")
                return

            # 添加结果到轨迹
            self.track_manager.add_camera_result(track.track_id, camera_result)

            # 保存相机结果到数据库
            await self.repository.save_camera_result({
                "track_id": track.track_id,
                "camera_id": payload.get("camera_id"),
                "result": "OK" if camera_result.result == "TRUE" else "FALSE",
                "code": camera_result.code,
                "symbology": camera_result.symbology,
                "ts_ms": camera_result.ts_ms
            })

            self.logger.info(f"[相机{payload.get('camera_id')}] 结果绑定到轨迹 {track.track_id}: "
                             f"code={camera_result.code}, success={camera_result.result == 'OK'}")

            track = self.track_manager.get_track_by_id(track.track_id)

            if len(track.camera_results) >= 2:
                # 调用决策引擎判定

                track.final_status = self.decision_engine.evaluate(track)

                # 设置最终码值
                successful = [r for r in track.camera_results if r.result == "TRUE"]
                track.final_code = successful[0].code if successful else None

                self.logger.info(f"[相机{payload.get('camera_id')}] 轨迹 {track.track_id} 判定完成: {track.final_status.value}")

                # 输出结果
                await self._output_result(track)

            else:
                self.logger.info(
                    f"[相机{payload.get('camera_id')}] 轨迹 {track.track_id} 已收到 {len(track.camera_results)}/2 个结果，继续等待")

        else:
            self.logger.info(f"get UNKNOWN")
    async def _on_camera_heartbeat(self, event: AppEvent) -> None:
        """处理相机心跳事件"""
        camera_id = event.payload.get("camera_id")
        status = event.payload.get("status")

        self.logger.debug(f"[相机{camera_id}] 心跳: {status}")

        # 通知 UI 更新相机状态
        await self._notify_ui_camera_status(camera_id, status)

    async def _on_track_timeout(self, event: AppEvent) -> None:
        """处理轨迹超时事件"""
        track_id = event.payload.get("track_id")

        track = self.track_manager.get_track_by_id(track_id)
        if track:
            track.final_status = DecisionStatus.TIMEOUT
            self.stats["timeout_count"] += 1

            await self._output_result(track)
            self.logger.warning(f"轨迹超时: {track_id}")

    async def _on_device_fault(self, event: AppEvent) -> None:
        """处理设备故障事件"""
        device_id = event.payload.get("device_id")
        device_type = event.payload.get("device_type")
        message = event.payload.get("message", "")

        self.logger.error(f"设备故障: {device_type}[{device_id}]: {message}")

        # 记录报警
        await self._raise_alarm(f"DEVICE_FAULT_{device_id}", message)

        # 通知 UI
        await self._notify_ui_device_fault(device_id, device_type, message)

    async def _output_result(self, track: BoxTrack) -> None:
        """
        """
        self.logger.info(f"[输出] 轨迹 {track.track_id}: "
                         f"状态={track.final_status.value}, 码值={track.final_code}")

        # 保存扫描记录到数据库
        await self.repository.save_scan_record({
            "track_id": track.track_id,
            "mode": track.mode.value,
            "final_code": track.final_code,
            "final_status": track.final_status.value,
            "created_ms": track.created_ms,
            "finalized_ts": time.time()
        })

        ## 上报到调度系统
        ## 上报到调度上位机
        if self.scheduler_client and self.scheduler_client.is_connected:
            report_payload = {
                "track_id": track.track_id,
                "mode": track.mode.value,
                "final_code": track.final_code,
                "status": track.final_status.value,
                "created_at": datetime.fromtimestamp(track.created_ms / 1_000).isoformat()
            }
            asyncio.create_task(self.scheduler_client.report_result(report_payload))

        # 上报到 MES
        if self.mes_client and self.mes_client.is_connected:
            mes_payload = {
                "track_id": track.track_id,
                "mode": track.mode.value,
                "final_code": track.final_code,
                "status": track.final_status.value,
                "created_at": datetime.fromtimestamp(track.created_ms / 1_000).isoformat(),
                "start_time": track.created_ms,
                "end_time": time.time()
            }
            asyncio.create_task(self.mes_client.report_scan_record(mes_payload))


        # 最终化轨迹（从活动列表移除）
        if track.final_status is None:
            self.logger.error(f"轨迹 {track.track_id} 的 final_status 为 None，使用 FAULT 状态")
            final_status = DecisionStatus.FAULT
        else:
            final_status = track.final_status

        self.track_manager.finalize_track(track.track_id, final_status)

        # 检查是否应该停止扫码会话
        if True:
            await self.scan_session_controller.stop_if_idle()

        # 通知 UI 更新
        await self._notify_ui_result(track)

    async def _cleanup_loop(self) -> None:
        """超时清理循环"""
        self.logger.info("清理循环启动")

        while self._running:
            try:
                await asyncio.sleep(1)  # 每秒检查一次

                # 清理超时轨迹
                now_ts = time.time()
                expired_tracks = self.track_manager.cleanup_expired(now_ts)

                for track in expired_tracks:
                    self.logger.warning(f"清理超时轨迹: {track.track_id}")
                    await self._output_result(track)

                # 清理已完成的轨迹（防止内存溢出）
                self.track_manager.clear_finished_tracks(max_keep=1000)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"清理循环异常: {e}", exc_info=True)

        self.logger.info("清理循环结束")

    async def _raise_alarm(self, code: str, message: str, level: str = "ERROR") -> None:
        """触发报警"""
        alarm = {
            "code": code,
            "level": level,
            "message": message,
            "created_ms": time.time()
        }

        # 保存到数据库
        await self.repository.save_alarm(alarm)

        # 发送报警事件到 UI
        await self._notify_ui_alarm(alarm)

        self.logger.warning(f"[报警] {code}: {message}")

    async def _reset_system(self) -> None:
        """重置系统"""
        self.logger.info("系统重置")
        self.track_manager.reset()
        self.stats = {
            "total_tracks": 0,
            "ok_count": 0,
            "ng_count": 0,
            "ambiguous_count": 0,
            "timeout_count": 0,
            "fault_count": 0
        }

    async def _clear_alarms(self) -> None:
        """清除报警"""
        self.logger.info("清除报警")
        # TODO: 实现报警清除逻辑

    # UI 通知方法（通过 EventBus 发送 UI 更新事件）
    async def _notify_ui_track_created(self, track: BoxTrack) -> None:
        """通知 UI 轨迹已创建"""
        self.event_bus.emit(EventType.UI_UPDATE, "runtime", {
            "type": "track_created",
            "track": {
                "track_id": track.track_id,
                "created_ms": track.created_ms,
                "status": track.status.value
            }
        })

    async def _notify_ui_window_opened(self, track: BoxTrack) -> None:
        """通知 UI 窗口已打开"""
        self.event_bus.emit(EventType.UI_UPDATE, "runtime", {
            "type": "window_opened",
            "track_id": track.track_id,
            "window_start": track.scan_window_start_ms,
            "window_end": track.scan_window_end_ms
        })

    async def _notify_ui_result(self, track: BoxTrack) -> None:
        """通知 UI 结果"""
        self.event_bus.emit(EventType.UI_UPDATE, "runtime", {
            "type": "result",
            "track_id": track.track_id,
            "status": track.final_status.value,
            "code": track.final_code,
            "stats": self.stats
        })

    async def _notify_ui_camera_status(self, camera_id: str, status: str) -> None:
        """通知 UI 相机状态"""
        self.event_bus.emit(EventType.UI_UPDATE, "runtime", {
            "type": "camera_status",
            "camera_id": camera_id,
            "status": status
        })

    async def _notify_ui_device_fault(self, device_id: str, device_type: str, message: str) -> None:
        """通知 UI 设备故障"""
        self.event_bus.emit(EventType.UI_UPDATE, "runtime", {
            "type": "device_fault",
            "device_id": device_id,
            "device_type": device_type,
            "message": message
        })

    async def _notify_ui_alarm(self, alarm: Dict) -> None:
        """通知 UI 报警"""
        self.event_bus.emit(EventType.UI_UPDATE, "runtime", {
            "type": "alarm",
            "alarm": alarm
        })

    # 属性
    @property
    def is_running(self) -> bool:
        """是否正在运行"""
        return self._running

    @property
    def current_mode(self) -> RunMode:
        """当前运行模式"""
        return self._current_mode

    @current_mode.setter
    def current_mode(self, mode: RunMode):
        """设置运行模式"""
        self._current_mode = mode
        self.logger.info(f"运行模式已设置为: {mode.value}")

    @property
    def current_stats(self) -> Dict:
        """获取当前统计"""
        return {
            **self.stats,
            "active_tracks": self.track_manager.active_count
        }

