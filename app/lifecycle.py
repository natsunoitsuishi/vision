import asyncio
import signal
from enum import Enum
from typing import Optional, Dict, List

from config import get_config
from config.manager import load_config
from devices import SchedulerClient, MesClient
from devices.camera import OptCameraClient
from devices.photoelectric import PhotoelectricClient
from domain.binder import ResultBinder
from domain.decision_engine import DecisionEngine
from domain.scan_session import ScanSessionController
from domain.scheduler import TriggerScheduler
from domain.track_manager import TrackManager
from infra.db.repository import SQLiteRepository
from infra.logging.setup import setup_logging, get_logger
from services import ArchiveService
from services.event_bus import EventBus
from services.runtime_service import RuntimeService


class AppState(Enum):
    """应用状态枚举"""
    BOOT = "BOOT"
    LOADING_CONFIG = "LOADING_CONFIG"
    INIT_INFRA = "INIT_INFRA"
    INIT_DEVICES = "INIT_DEVICES"
    INIT_BUSINESS = "INIT_BUSINESS"
    INIT_RUNTIME = "INIT_RUNTIME"
    INIT_UI = "INIT_UI"
    READY = "READY"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"
    FAULT = "FAULT"


class AppController:
    """
    应用控制器 - 负责应用的整个生命周期

    职责：
    1. 管理应用启动、运行和关闭流程
    2. 协调所有模块的初始化和依赖注入
    3. 处理系统信号和异常
    4. 维护应用状态机
    5. 提供降级和故障恢复能力
    """

    def __init__(self):
        """
        初始化应用控制器

        Args:
            qt_app: Qt 应用实例
            loop: asyncio 事件循环
        """
        # self.qt_app = qt_app

        # 应用状态
        self.state = AppState.BOOT
        self._shutdown_event = asyncio.Event()
        self._startup_time = 0.0

        # 日志
        self.logger = get_logger(__name__)

        # 配置和基础设施
        #  --- config_manage 全局单例 ---
        self.repository: Optional[SQLiteRepository] = None
        self.event_bus: Optional[EventBus] = None
        # self.alarm_service: Optional[AlarmService] = None

        # 设备服务
        self.photoelectric_client: Optional[PhotoelectricClient] = None
        self.cameras: Dict[str, OptCameraClient] = {}

        # 业务领域服务
        self.track_manager: Optional[TrackManager] = None
        self.trigger_scheduler: Optional[TriggerScheduler] = None
        self.scan_session_controller: Optional[ScanSessionController] = None
        self.result_binder: Optional[ResultBinder] = None
        self.decision_engine: Optional[DecisionEngine] = None

        # 对外接口
        self.scheduler_client: Optional[SchedulerClient] = None
        self.mes_client: Optional[MesClient] = None

        # 运行时服务
        self.runtime_service: Optional[RuntimeService] = None
        # self.health_service: Optional[HealthService] = None
        self.archive_service: Optional[ArchiveService] = None

        # UI
        # self.main_window: Optional[MainWindow] = None

        # 后台任务
        self._background_tasks: List[asyncio.Task] = []

    #     # 注册信号处理
    #     self._register_signal_handlers()
    #
    # def _register_signal_handlers(self) -> None:
    #     """注册系统信号处理器"""
    #     for sig in (signal.SIGINT, signal.SIGTERM):
    #         try:
    #             self.loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))
    #         except NotImplementedError:
    #             # Windows 不支持 add_signal_handler
    #             signal.signal(sig, lambda s, f: asyncio.create_task(self.shutdown()))

        # 注册信号处理
        self._register_signal_handlers()

    # ====================== 关键修改：无GUI信号处理（跨平台稳定） ======================
    def _register_signal_handlers(self) -> None:
        """注册系统信号处理器（纯asyncio版，无Qt依赖）"""
        async def shutdown_handler():
            await self.shutdown()

        def signal_callback(sig_num, frame):
            asyncio.create_task(shutdown_handler())

        # 注册 Ctrl+C 和系统关闭信号
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, signal_callback)

    async def startup(self) -> None:
        """
        应用启动入口，按依赖顺序初始化所有模块

        启动顺序：
        1. 基础设施层（配置、日志、数据库）
        2. 领域服务层（仓储、事件总线、报警）
        3. 设备驱动层（DI/O、相机）
        4. 业务编排层（轨迹管理、调度、绑定、决策）
        5. 对外接口层（调度、PLC、MES）
        6. 运行时服务层（主业务循环）
        7. 后台服务层（健康检查、归档）
        8. UI 层（主窗口）
        """
        try:
            # 1. 基础设施层 - 无外部依赖，必须最先启动
            setup_logging()
            log = get_logger(__name__)

            self.state = AppState.LOADING_CONFIG
            await load_config()

            # --- database ---
            self.state = AppState.INIT_INFRA
            self.repository = SQLiteRepository()

            # 异步检查连接 ✅ 正确写法
            ok, msg = await self.repository.initialize_database()
            if not ok:
                raise ConnectionError(msg)
            self.event_bus = EventBus()
            self.event_bus.start()
            # await self._init_alarm_service()

            # 2. 设备驱动层 - 依赖配置和事件总线
            self.state = AppState.INIT_DEVICES
            cam1 = OptCameraClient(1, self.event_bus)
            await cam1.connect()
            await cam1.start_scan_session()
            self.cameras["CAM1"] = cam1
            # cam2 = OptCameraClient(2, self.event_bus)
            # await cam2.connect()
            # await cam2.start_scan_session()
            # self.cameras["CAM2"] = cam2

            self.photoelectric_client = PhotoelectricClient(self.event_bus)
            await self.photoelectric_client.connect()

            # # 3. 业务编排层 - 依赖领域服务和设备驱动
            self.state = AppState.INIT_BUSINESS
            self.track_manager = TrackManager()
            self.trigger_scheduler = TriggerScheduler()
            self.scan_session_controller = ScanSessionController(self.cameras)
            self.result_binder = ResultBinder()
            self.decision_engine = DecisionEngine()

            # 4. 对外接口层 - 依赖配置和业务模块
            # 创建调度客户端
            self.scheduler_client = SchedulerClient(
                host=get_config("scheduler_client.host"),
                port=get_config("scheduler_client.port"),
                device_id=get_config("scheduler_client.id")
            )
            await self.scheduler_client.connect()
            self.logger.info("调度上位机客户端已启动")

            # 创建 MES 客户端
            self.mes_client = MesClient(
                host=get_config("mes.host"),
                port=get_config("mes.port"),
                device_id=get_config("mes_client.id"),
                line_id=get_config("mes_client.line_id")
            )
            await self.mes_client.connect()
            self.logger.info("MES 客户端已启动")

            # 位置推算服务
            self.archive_service = ArchiveService(self.event_bus)
            # await self.archive_service.start()

            # 5. 运行时服务层 - 整合所有模块，启动核心业务循环
            self.state = AppState.INIT_RUNTIME
            self.runtime_service = RuntimeService(
                self.event_bus,
                self.track_manager,
                self.trigger_scheduler,
                self.scan_session_controller,
                self.result_binder,
                self.decision_engine,
                self.photoelectric_client,
                self.cameras,
                self.repository,
                self.scheduler_client,
                self.mes_client,
                self.archive_service
            )
            await self.runtime_service.start()

            ## 6. 后台服务层 - 辅助服务，不影响主业务
            # await self._init_health_service()
            # await self._init_archive_service()

            ## 7. UI 层 - 最后启动，确保后台已就绪
            self.state = AppState.INIT_UI
            # await self._init_main_window()

            # 8. 启动完成
            self.state = AppState.READY
            self.logger.info("Application startup completed successfully")

            self.state = AppState.RUNNING
            self.logger.info("Application is now running")

        except Exception as e:
            self.logger.critical(f"Startup failed: {e}", exc_info=True)
            self.state = AppState.FAULT
            await self._handle_startup_failure(e)
            raise

    async def _start_runtime(self) -> None:
        """启动运行时服务"""
        await self.runtime_service.start()
        self.logger.info("Runtime service started")

    async def _handle_startup_failure(self, error: Exception) -> None:
        """处理启动失败"""
        self.logger.error(f"Startup failure: {error}")

        # 尝试显示错误对话框（如果 UI 可用）
        if self.main_window:
            # 通过 Qt 信号显示错误
            pass
        else:
            # 输出到控制台
            print(f"FATAL: Application startup failed - {error}")

    async def shutdown(self) -> None:
        pass
        """
        优雅关闭应用

        关闭顺序与启动顺序相反
        """
        if self.state == AppState.STOPPING:
            return

        self.state = AppState.STOPPING
        self.logger.info("Shutting down application...")

        # 1. 停止运行时服务（停止接收新事件）
        if self.runtime_service:
            await self.runtime_service.stop()
            self.logger.info("Runtime service stopped")

        # 2. 停止后台服务
        for task in self._background_tasks:
            task.cancel()

        # 等待后台任务完成
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self.logger.info("Background services stopped")

        # 3. 关闭对外接口
        if self.scheduler_client:
            await self.scheduler_client.disconnect()
        self.logger.info("External clients disconnected")

        for camera in self.cameras.values():
            await camera.disconnect()
        self.logger.info("Devices stopped")

        # 5. 关闭数据库连接
        if self.repository:
            await self.repository.close()

        # 6. 关闭日志
        self.logger.shutdown()

        # 7. 关闭 UI
        if self.main_window:
            self.main_window.close()

        # 8. 退出事件循环
        self._shutdown_event.set()
        self.logger.info("Application shutdown completed")

        # # 退出 Qt 应用
        # self.qt_app.quit()

    # async def restart_runtime(self) -> None:
    #     """
    #     重启运行时服务（用于配置热更新）
    #     """
    #     self.logger.info("Restarting runtime service...")
    #
    #     # 停止当前运行时
    #     if self.runtime_service:
    #         await self.runtime_service.stop()
    #
    #     # 重新加载配置
    #     await self.config_manager.reload()
    #     self.config = self.config_manager.get_config()
    #
    #     # 重新初始化依赖配置的组件
    #     await self._init_track_manager()
    #     await self._init_trigger_scheduler()
    #     await self._init_scan_session_controller()
    #     await self._init_result_binder()
    #
    #     # 重启运行时
    #     await self._init_runtime_service()
    #     await self._start_runtime()
    #
    #     self.logger.info("Runtime service restarted")

    def get_state(self) -> AppState:
        """获取当前应用状态"""
        return self.state

    def is_ready(self) -> bool:
        """检查应用是否就绪"""
        return self.state in (AppState.READY, AppState.RUNNING)

    def is_running(self) -> bool:
        """检查应用是否在运行"""
        return self.state == AppState.RUNNING

    async def _health_check_loop(self):
        pass
        # """健康检查循环"""
        while True:
            try:
                await asyncio.sleep(30)  # 每30秒检查一次
                if self.runtime:
                    health_status = await self.runtime.health_check()
                    if not health_status["healthy"]:
                        self.logger.warning(f"健康检查异常: {health_status}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"健康检查失败: {e}")
