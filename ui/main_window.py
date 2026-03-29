# ui/main_window.py
"""
主窗口 - 扫码视觉门上位机界面
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, Any

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget,
    QGroupBox, QFrame, QHeaderView, QMessageBox, QSystemTrayIcon,
    QMenu, QApplication
)
from PySide6.QtCore import Qt, QTimer, Signal, QSize, QDateTime
from PySide6.QtGui import QFont, QPalette, QColor, QIcon, QAction

from services.event_bus import EventBus
from domain.enums import EventType, DecisionStatus, DeviceStatus, RunMode


class MainWindow(QMainWindow):
    """扫码视觉门主窗口"""

    # 定义信号（用于跨线程通信）
    update_stats_signal = Signal(dict)
    update_camera_status_signal = Signal(str, str)
    update_pe_status_signal = Signal(str, bool)
    add_result_signal = Signal(dict)
    add_alarm_signal = Signal(dict)
    update_active_tracks_signal = Signal(int)

    def __init__(self, event_bus: EventBus = None, runtime_service=None):
        """
        初始化主窗口

        Args:
            event_bus: 事件总线
            runtime_service: 运行时服务（用于获取统计数据）
        """
        super().__init__()

        self.event_bus = event_bus
        self.runtime_service = runtime_service
        self.logger = logging.getLogger(__name__)

        # 窗口状态
        self._is_fullscreen = False
        self._current_mode = RunMode.LR

        # 统计数据缓存
        self._stats = {
            "total_tracks": 0,
            "ok_count": 0,
            "ng_count": 0,
            "ambiguous_count": 0,
            "timeout_count": 0,
            "fault_count": 0,
            "active_tracks": 0
        }

        # 相机状态缓存
        self._camera_status = {
            "CAM1": {"status": "未知", "online": False},
            "CAM2": {"status": "未知", "online": False},
        }

        # PE状态缓存
        self._pe_status = {
            "PE1": False,
            "PE2": False,
        }

        # 最近结果列表（最多50条）
        self._recent_results = []

        # 报警列表
        self._active_alarms = []

        # 连接信号
        self.update_stats_signal.connect(self._on_update_stats)
        self.update_camera_status_signal.connect(self._on_update_camera_status)
        self.update_pe_status_signal.connect(self._on_update_pe_status)
        self.add_result_signal.connect(self._on_add_result)
        self.add_alarm_signal.connect(self._on_add_alarm)
        self.update_active_tracks_signal.connect(self._on_update_active_tracks)

        # 设置UI
        self._setup_ui()
        self._apply_style()

        # 启动定时刷新
        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(self._refresh_stats)
        self._refresh_timer.start(500)  # 每500ms刷新一次

        # 订阅事件总线（如果提供）
        if self.event_bus:
            self._subscribe_events()

        self.logger.info("主窗口初始化完成")

    def _setup_ui(self):
        """设置UI布局"""
        self.setWindowTitle("扫码视觉门上位机 V1.0")
        self.setMinimumSize(1024, 768)

        # 创建中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # 主布局
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # 顶部状态栏
        top_bar = self._create_top_bar()
        main_layout.addWidget(top_bar)

        # 创建标签页
        self.tab_widget = QTabWidget()
        self.tab_widget.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #3c3c3c;
                background-color: #2d2d2d;
            }
            QTabBar::tab {
                background-color: #3c3c3c;
                color: #ffffff;
                padding: 8px 16px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background-color: #4a6ea5;
            }
            QTabBar::tab:hover {
                background-color: #4a6ea5;
            }
        """)

        # 监控页
        self.dashboard_tab = self._create_dashboard_tab()
        self.tab_widget.addTab(self.dashboard_tab, "监控")

        # 设备页
        self.device_tab = self._create_device_tab()
        self.tab_widget.addTab(self.device_tab, "设备")

        # 历史页
        self.history_tab = self._create_history_tab()
        self.tab_widget.addTab(self.history_tab, "历史")

        # 报警页
        self.alarm_tab = self._create_alarm_tab()
        self.tab_widget.addTab(self.alarm_tab, "报警")

        # 配置页
        self.config_tab = self._create_config_tab()
        self.tab_widget.addTab(self.config_tab, "配置")

        main_layout.addWidget(self.tab_widget)

        # 底部状态栏
        bottom_bar = self._create_bottom_bar()
        main_layout.addWidget(bottom_bar)

    def _create_top_bar(self) -> QFrame:
        """创建顶部栏"""
        frame = QFrame()
        frame.setFixedHeight(60)
        frame.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d;
                border-bottom: 1px solid #3c3c3c;
            }
        """)

        layout = QHBoxLayout(frame)
        layout.setContentsMargins(20, 0, 20, 0)

        # 标题
        title = QLabel("📷 扫码视觉门控制系统")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #ffffff;")
        layout.addWidget(title)

        layout.addStretch()

        # 运行模式显示
        self.mode_label = QLabel("模式: LR")
        self.mode_label.setStyleSheet("""
            QLabel {
                background-color: #4a6ea5;
                padding: 5px 15px;
                border-radius: 5px;
                font-weight: bold;
            }
        """)
        layout.addWidget(self.mode_label)

        # 会话状态
        self.session_label = QLabel("扫码会话: 空闲")
        self.session_label.setStyleSheet("""
            QLabel {
                background-color: #5a5a5a;
                padding: 5px 15px;
                border-radius: 5px;
            }
        """)
        layout.addWidget(self.session_label)

        # 时间显示
        self.time_label = QLabel()
        self.time_label.setStyleSheet("color: #cccccc;")
        layout.addWidget(self.time_label)

        # 时间定时器
        self._time_timer = QTimer()
        self._time_timer.timeout.connect(self._update_time)
        self._time_timer.start(1000)
        self._update_time()

        return frame

    def _create_dashboard_tab(self) -> QWidget:
        """创建监控页"""
        widget = QWidget()
        layout = QGridLayout(widget)
        layout.setSpacing(15)

        # 统计卡片区域
        stats_frame = QFrame()
        stats_frame.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        stats_layout = QHBoxLayout(stats_frame)

        # 统计卡片
        self.stats_widgets = {}
        stats_items = [
            ("total", "总鞋盒", "0", "#4a6ea5"),
            ("ok", "成功", "0", "#2e7d32"),
            ("ng", "未读", "0", "#c62828"),
            ("ambiguous", "歧义", "0", "#f57c00"),
            ("timeout", "超时", "0", "#9c27b0"),
            ("fault", "故障", "0", "#d32f2f"),
        ]

        for key, title, value, color in stats_items:
            card = self._create_stat_card(title, value, color)
            stats_layout.addWidget(card)
            self.stats_widgets[key] = card

        layout.addWidget(stats_frame, 0, 0, 1, 2)

        # 活动轨迹数量
        active_frame = QFrame()
        active_frame.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d;
                border-radius: 8px;
            }
        """)
        active_layout = QHBoxLayout(active_frame)
        active_layout.addWidget(QLabel("活动轨迹数:"))
        self.active_tracks_label = QLabel("0")
        self.active_tracks_label.setStyleSheet("font-size: 24px; font-weight: bold; color: #4a6ea5;")
        active_layout.addWidget(self.active_tracks_label)
        active_layout.addStretch()
        layout.addWidget(active_frame, 1, 0)

        # 最近结果表格
        result_group = QGroupBox("最近扫码结果")
        result_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #3c3c3c;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
        """)
        result_layout = QVBoxLayout(result_group)

        self.result_table = QTableWidget()
        self.result_table.setColumnCount(5)
        self.result_table.setHorizontalHeaderLabels(["时间", "轨迹ID", "码值", "状态", "处理时间(ms)"])
        self.result_table.horizontalHeader().setStretchLastSection(True)
        self.result_table.setAlternatingRowColors(True)
        self.result_table.setStyleSheet("""
            QTableWidget {
                background-color: #2d2d2d;
                alternate-background-color: #353535;
                gridline-color: #3c3c3c;
            }
            QTableWidget::item {
                padding: 5px;
            }
        """)
        result_layout.addWidget(self.result_table)

        layout.addWidget(result_group, 2, 0, 1, 2)

        # 报警区域
        alarm_group = QGroupBox("当前报警")
        alarm_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #3c3c3c;
                border-radius: 5px;
                margin-top: 10px;
            }
        """)
        alarm_layout = QVBoxLayout(alarm_group)

        self.alarm_list = QLabel("暂无报警")
        self.alarm_list.setStyleSheet("color: #888888; padding: 10px;")
        self.alarm_list.setWordWrap(True)
        alarm_layout.addWidget(self.alarm_list)

        layout.addWidget(alarm_group, 3, 0, 1, 2)

        # 设置行和列的比例
        layout.setRowStretch(0, 1)
        layout.setRowStretch(1, 0)
        layout.setRowStretch(2, 3)
        layout.setRowStretch(3, 1)

        return widget

    def _create_stat_card(self, title: str, value: str, color: str) -> QFrame:
        """创建统计卡片"""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: #3c3c3c;
                border-radius: 8px;
                padding: 10px;
            }}
        """)
        layout = QVBoxLayout(card)

        title_label = QLabel(title)
        title_label.setStyleSheet("color: #cccccc; font-size: 12px;")
        layout.addWidget(title_label)

        value_label = QLabel(value)
        value_label.setStyleSheet(f"color: {color}; font-size: 24px; font-weight: bold;")
        layout.addWidget(value_label)

        # 保存value label的引用
        card.value_label = value_label

        return card

    def _create_device_tab(self) -> QWidget:
        """创建设备页"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 相机状态
        camera_group = QGroupBox("相机状态")
        camera_layout = QGridLayout(camera_group)

        self.camera_widgets = {}
        cameras = [
            ("CAM1", "相机 A"),
            ("CAM2", "相机 B"),
        ]

        for i, (cam_id, cam_name) in enumerate(cameras):
            # 相机名称
            name_label = QLabel(cam_name)
            name_label.setStyleSheet("font-weight: bold;")
            camera_layout.addWidget(name_label, i, 0)

            # 状态
            status_label = QLabel("检测中...")
            status_label.setStyleSheet("color: #ff9800;")
            camera_layout.addWidget(status_label, i, 1)
            self.camera_widgets[f"{cam_id}_status"] = status_label

            # IP地址
            ip_label = QLabel("IP: 192.168.1.79")
            ip_label.setStyleSheet("color: #888888;")
            camera_layout.addWidget(ip_label, i, 2)

            # 重连按钮
            reconnect_btn = QPushButton("重连")
            reconnect_btn.setFixedWidth(60)
            reconnect_btn.clicked.connect(lambda checked, c=cam_id: self._reconnect_camera(c))
            camera_layout.addWidget(reconnect_btn, i, 3)

        layout.addWidget(camera_group)

        # PE状态
        pe_group = QGroupBox("光电传感器状态")
        pe_layout = QGridLayout(pe_group)

        self.pe_widgets = {}
        pes = [("PE1", "入口光电"), ("PE2", "出口光电")]

        for i, (pe_id, pe_name) in enumerate(pes):
            name_label = QLabel(pe_name)
            name_label.setStyleSheet("font-weight: bold;")
            pe_layout.addWidget(name_label, i, 0)

            status_label = QLabel("未触发")
            status_label.setStyleSheet("color: #888888;")
            pe_layout.addWidget(status_label, i, 1)
            self.pe_widgets[f"{pe_id}_status"] = status_label

        layout.addWidget(pe_group)

        # 系统信息
        system_group = QGroupBox("系统信息")
        system_layout = QGridLayout(system_group)

        system_info = [
            ("CPU使用率:", "--%"),
            ("内存使用率:", "--%"),
            ("运行时间:", "--"),
            ("事件队列:", "--"),
        ]

        for i, (key, value) in enumerate(system_info):
            key_label = QLabel(key)
            key_label.setStyleSheet("color: #cccccc;")
            system_layout.addWidget(key_label, i, 0)

            value_label = QLabel(value)
            system_layout.addWidget(value_label, i, 1)
            self.system_widgets = getattr(self, 'system_widgets', {})
            self.system_widgets[key] = value_label

        layout.addWidget(system_group)

        layout.addStretch()

        return widget

    def _create_history_tab(self) -> QWidget:
        """创建历史页"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 查询条件
        filter_layout = QHBoxLayout()

        filter_layout.addWidget(QLabel("开始时间:"))
        self.start_time_edit = QLabel("--")
        filter_layout.addWidget(self.start_time_edit)

        filter_layout.addWidget(QLabel("结束时间:"))
        self.end_time_edit = QLabel("--")
        filter_layout.addWidget(self.end_time_edit)

        filter_layout.addWidget(QLabel("状态:"))
        self.status_combo = QLabel("全部")
        filter_layout.addWidget(self.status_combo)

        filter_layout.addStretch()

        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._refresh_history)
        filter_layout.addWidget(refresh_btn)

        layout.addLayout(filter_layout)

        # 历史表格
        self.history_table = QTableWidget()
        self.history_table.setColumnCount(6)
        self.history_table.setHorizontalHeaderLabels(["时间", "轨迹ID", "码值", "状态", "处理时间(ms)", "模式"])
        self.history_table.horizontalHeader().setStretchLastSection(True)
        self.history_table.setAlternatingRowColors(True)
        layout.addWidget(self.history_table)

        # 导出按钮
        export_btn = QPushButton("导出CSV")
        export_btn.setFixedWidth(100)
        export_btn.clicked.connect(self._export_history)
        layout.addWidget(export_btn, alignment=Qt.AlignmentFlag.AlignRight)

        return widget

    def _create_alarm_tab(self) -> QWidget:
        """创建报警页"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 报警表格
        self.alarm_table = QTableWidget()
        self.alarm_table.setColumnCount(6)
        self.alarm_table.setHorizontalHeaderLabels(["时间", "报警码", "级别", "描述", "状态", "确认人"])
        self.alarm_table.horizontalHeader().setStretchLastSection(True)
        self.alarm_table.setAlternatingRowColors(True)
        layout.addWidget(self.alarm_table)

        # 清除按钮
        clear_btn = QPushButton("清除历史报警")
        clear_btn.setFixedWidth(120)
        clear_btn.clicked.connect(self._clear_alarms)
        layout.addWidget(clear_btn, alignment=Qt.AlignmentFlag.AlignRight)

        return widget

    def _create_config_tab(self) -> QWidget:
        """创建配置页"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 运行模式
        mode_group = QGroupBox("运行模式")
        mode_layout = QHBoxLayout(mode_group)

        self.lr_radio = QLabel("LR模式")
        self.fb_radio = QLabel("FB模式")

        mode_layout.addWidget(self.lr_radio)
        mode_layout.addWidget(self.fb_radio)
        mode_layout.addStretch()

        layout.addWidget(mode_group)

        # 系统控制
        control_group = QGroupBox("系统控制")
        control_layout = QHBoxLayout(control_group)

        reset_btn = QPushButton("重置系统")
        reset_btn.setStyleSheet("background-color: #c62828;")
        reset_btn.clicked.connect(self._reset_system)
        control_layout.addWidget(reset_btn)

        restart_btn = QPushButton("重启服务")
        restart_btn.clicked.connect(self._restart_service)
        control_layout.addWidget(restart_btn)

        layout.addWidget(control_group)

        layout.addStretch()

        return widget

    def _create_bottom_bar(self) -> QFrame:
        """创建底部栏"""
        frame = QFrame()
        frame.setFixedHeight(35)
        frame.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d;
                border-top: 1px solid #3c3c3c;
            }
        """)

        layout = QHBoxLayout(frame)
        layout.setContentsMargins(10, 0, 10, 0)

        # 状态信息
        self.status_label = QLabel("系统就绪")
        self.status_label.setStyleSheet("color: #4caf50;")
        layout.addWidget(self.status_label)

        layout.addStretch()

        # 全屏按钮
        fullscreen_btn = QPushButton("全屏")
        fullscreen_btn.setFlat(True)
        fullscreen_btn.clicked.connect(self._toggle_fullscreen)
        layout.addWidget(fullscreen_btn)

        return frame

    def _apply_style(self):
        """应用全局样式"""
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1e1e1e;
            }
            QLabel {
                color: #ffffff;
            }
            QPushButton {
                background-color: #4a6ea5;
                color: white;
                border: none;
                padding: 5px 10px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #5d8abf;
            }
            QPushButton:pressed {
                background-color: #3a5a8a;
            }
            QGroupBox {
                color: #ffffff;
                border: 1px solid #3c3c3c;
                border-radius: 5px;
                margin-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
        """)

    def _create_stat_card(self, title: str, value: str, color: str) -> QFrame:
        """创建统计卡片"""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: #2d2d2d;
                border-radius: 8px;
                padding: 10px;
                border: 1px solid #3c3c3c;
            }}
        """)
        layout = QVBoxLayout(card)
        layout.setSpacing(5)

        title_label = QLabel(title)
        title_label.setStyleSheet("color: #aaaaaa; font-size: 12px;")
        layout.addWidget(title_label)

        value_label = QLabel(value)
        value_label.setStyleSheet(f"color: {color}; font-size: 28px; font-weight: bold;")
        layout.addWidget(value_label)

        # 保存value label的引用
        card.value_label = value_label

        return card

    def _subscribe_events(self):
        """订阅事件总线"""
        if not self.event_bus:
            return

        # 订阅UI更新事件
        self.event_bus.subscribe(EventType.UI_UPDATE, self._on_ui_event)

    async def _on_ui_event(self, event):
        """处理UI事件"""
        payload = event.payload
        event_type = payload.get("type")

        if event_type == "track_created":
            track = payload.get("track", {})
            self.update_active_tracks_signal.emit(1)

        elif event_type == "result":
            # 更新统计
            stats = payload.get("stats", {})
            self.update_stats_signal.emit(stats)

            # 添加结果到列表
            self.add_result_signal.emit({
                "track_id": payload.get("track_id"),
                "status": payload.get("status"),
                "code": payload.get("code"),
                "stats": stats
            })

        elif event_type == "camera_status":
            camera_id = payload.get("camera_id")
            status = payload.get("status")
            self.update_camera_status_signal.emit(camera_id, status)

        elif event_type == "alarm":
            alarm = payload.get("alarm", {})
            self.add_alarm_signal.emit(alarm)

        elif event_type == "device_fault":
            device_id = payload.get("device_id")
            message = payload.get("message")
            self.add_alarm_signal.emit({
                "code": f"DEVICE_FAULT_{device_id}",
                "level": "ERROR",
                "message": message
            })

    def _on_update_stats(self, stats: dict):
        """更新统计显示"""
        self._stats.update(stats)

        # 更新统计卡片
        self.stats_widgets["total"].value_label.setText(str(self._stats.get("total_tracks", 0)))
        self.stats_widgets["ok"].value_label.setText(str(self._stats.get("ok_count", 0)))
        self.stats_widgets["ng"].value_label.setText(str(self._stats.get("ng_count", 0)))
        self.stats_widgets["ambiguous"].value_label.setText(str(self._stats.get("ambiguous_count", 0)))
        self.stats_widgets["timeout"].value_label.setText(str(self._stats.get("timeout_count", 0)))
        self.stats_widgets["fault"].value_label.setText(str(self._stats.get("fault_count", 0)))

    def _on_update_camera_status(self, camera_id: str, status: str):
        """更新相机状态显示"""
        self._camera_status[camera_id]["status"] = status
        self._camera_status[camera_id]["online"] = (status == "ONLINE")

        if camera_id in self.camera_widgets:
            widget = self.camera_widgets[f"{camera_id}_status"]
            if status == "ONLINE":
                widget.setText("在线")
                widget.setStyleSheet("color: #4caf50;")
            elif status == "OFFLINE":
                widget.setText("离线")
                widget.setStyleSheet("color: #f44336;")
            else:
                widget.setText(status)
                widget.setStyleSheet("color: #ff9800;")

    def _on_update_pe_status(self, pe_id: str, state: bool):
        """更新PE状态显示"""
        self._pe_status[pe_id] = state

        if pe_id in self.pe_widgets:
            widget = self.pe_widgets[f"{pe_id}_status"]
            if state:
                widget.setText("触发")
                widget.setStyleSheet("color: #4caf50; font-weight: bold;")
            else:
                widget.setText("未触发")
                widget.setStyleSheet("color: #888888;")

    def _on_add_result(self, result: dict):
        """添加结果到列表"""
        # 添加到缓存
        self._recent_results.insert(0, {
            "time": QDateTime.currentDateTime().toString("HH:mm:ss"),
            "track_id": result.get("track_id", "")[-12:],
            "code": result.get("code", "") or "--",
            "status": result.get("status", "--"),
            "process_time": "--"
        })

        # 限制数量
        if len(self._recent_results) > 50:
            self._recent_results.pop()

        # 刷新表格
        self.result_table.setRowCount(len(self._recent_results))
        for i, r in enumerate(self._recent_results):
            self.result_table.setItem(i, 0, QTableWidgetItem(r["time"]))
            self.result_table.setItem(i, 1, QTableWidgetItem(r["track_id"]))
            self.result_table.setItem(i, 2, QTableWidgetItem(r["code"]))
            self.result_table.setItem(i, 3, QTableWidgetItem(r["status"]))

            # 根据状态设置颜色
            status_item = self.result_table.item(i, 3)
            if r["status"] == "OK":
                status_item.setForeground(QColor("#4caf50"))
            elif r["status"] in ("NO_READ", "TIMEOUT"):
                status_item.setForeground(QColor("#f44336"))
            elif r["status"] == "AMBIGUOUS":
                status_item.setForeground(QColor("#ff9800"))
            else:
                status_item.setForeground(QColor("#9c27b0"))

        # 滚动到顶部
        self.result_table.scrollToTop()

    def _on_add_alarm(self, alarm: dict):
        """添加报警"""
        alarm_time = QDateTime.currentDateTime().toString("HH:mm:ss")
        alarm_code = alarm.get("code", "UNKNOWN")
        level = alarm.get("level", "INFO")
        message = alarm.get("message", "")

        # 添加到报警列表
        self._active_alarms.insert(0, {
            "time": alarm_time,
            "code": alarm_code,
            "level": level,
            "message": message
        })

        # 更新显示
        if self._active_alarms:
            latest = self._active_alarms[0]
            self.alarm_list.setText(f"⚠ {latest['code']}: {latest['message']}")
            self.alarm_list.setStyleSheet("color: #f44336; padding: 10px;")
            self.status_label.setText(f"报警: {latest['code']}")
            self.status_label.setStyleSheet("color: #f44336;")
        else:
            self.alarm_list.setText("暂无报警")
            self.alarm_list.setStyleSheet("color: #888888; padding: 10px;")
            self.status_label.setText("系统就绪")
            self.status_label.setStyleSheet("color: #4caf50;")

    def _on_update_active_tracks(self, delta: int):
        """更新活动轨迹数"""
        current = int(self.active_tracks_label.text())
        new_count = max(0, current + delta)
        self.active_tracks_label.setText(str(new_count))

        # 更新会话状态显示
        if new_count > 0:
            self.session_label.setText("扫码会话: 运行中")
            self.session_label.setStyleSheet("""
                QLabel {
                    background-color: #4caf50;
                    padding: 5px 15px;
                    border-radius: 5px;
                    color: white;
                }
            """)
        else:
            self.session_label.setText("扫码会话: 空闲")
            self.session_label.setStyleSheet("""
                QLabel {
                    background-color: #5a5a5a;
                    padding: 5px 15px;
                    border-radius: 5px;
                }
            """)

    def _refresh_stats(self):
        """刷新统计数据"""
        if self.runtime_service:
            stats = self.runtime_service.current_stats
            self.update_stats_signal.emit(stats)

            # 更新活动轨迹数
            active = stats.get("active_tracks", 0)
            if self.active_tracks_label.text() != str(active):
                self.active_tracks_label.setText(str(active))

            # 更新会话状态
            if active > 0:
                if self.session_label.text() != "扫码会话: 运行中":
                    self.session_label.setText("扫码会话: 运行中")
                    self.session_label.setStyleSheet("""
                        QLabel {
                            background-color: #4caf50;
                            padding: 5px 15px;
                            border-radius: 5px;
                            color: white;
                        }
                    """)
            else:
                if self.session_label.text() != "扫码会话: 空闲":
                    self.session_label.setText("扫码会话: 空闲")
                    self.session_label.setStyleSheet("""
                        QLabel {
                            background-color: #5a5a5a;
                            padding: 5px 15px;
                            border-radius: 5px;
                        }
                    """)

    def _refresh_history(self):
        """刷新历史记录"""
        # TODO: 从数据库加载历史记录
        pass

    def _export_history(self):
        """导出历史记录"""
        # TODO: 导出CSV文件
        QMessageBox.information(self, "提示", "导出功能开发中")

    def _clear_alarms(self):
        """清除报警"""
        self._active_alarms.clear()
        self.alarm_list.setText("暂无报警")
        self.alarm_list.setStyleSheet("color: #888888; padding: 10px;")

    def _reset_system(self):
        """重置系统"""
        reply = QMessageBox.question(
            self, "确认重置",
            "确定要重置系统吗？所有统计数据将被清零。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            if self.runtime_service:
                asyncio.create_task(self.runtime_service._reset_system())
            self._stats = {k: 0 for k in self._stats}
            self._recent_results.clear()
            self.result_table.setRowCount(0)
            self.update_stats_signal.emit(self._stats)
            QMessageBox.information(self, "提示", "系统已重置")

    def _restart_service(self):
        """重启服务"""
        reply = QMessageBox.question(
            self, "确认重启",
            "确定要重启服务吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            # TODO: 实现服务重启
            QMessageBox.information(self, "提示", "服务重启功能开发中")

    def _reconnect_camera(self, camera_id: str):
        """重连相机"""
        QMessageBox.information(self, "提示", f"正在重连{camera_id}...")

    def _update_time(self):
        """更新时间显示"""
        self.time_label.setText(QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss"))

    def _toggle_fullscreen(self):
        """切换全屏"""
        if self._is_fullscreen:
            self.showNormal()
            self._is_fullscreen = False
        else:
            self.showFullScreen()
            self._is_fullscreen = True

    def closeEvent(self, event):
        """关闭事件"""
        reply = QMessageBox.question(
            self, "确认退出",
            "确定要退出程序吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            event.accept()
        else:
            event.ignore()

    def update_pe_status(self, pe_id: str, state: bool):
        """外部调用：更新PE状态"""
        self.update_pe_status_signal.emit(pe_id, state)