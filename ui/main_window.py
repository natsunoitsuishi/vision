# ui/main_window.py
"""
视觉门控制系统 - 主窗口

通过 EventBus 订阅 UI_UPDATE 事件来实时更新界面
"""

import asyncio
from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum

from PySide2.QtCore import Qt, QTimer, Signal
from PySide2.QtGui import QFont, QColor
from PySide2.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QFrame,
    QScrollArea, QMessageBox, QSplitter, QProgressBar
)

from qasync import asyncSlot

from domain.enums import EventType, DecisionStatus
from domain.models import AppEvent
from services.event_bus import EventBus

# =============================
# 样式表
# =============================

STYLE = """
QMainWindow {
    background-color: #1a1a2e;
}

/* 卡片 */
.card {
    background-color: #252542;
    border-radius: 12px;
    padding: 12px;
}

.card-title {
    font-size: 13px;
    font-weight: 600;
    color: #a0a0c0;
    padding-bottom: 8px;
    border-bottom: 1px solid #353570;
}

/* 统计数值 */
.stat-value {
    font-size: 28px;
    font-weight: bold;
    color: #ffffff;
}

.stat-label {
    font-size: 12px;
    color: #8080a0;
}

/* 状态点 */
.dot {
    border-radius: 6px;
    min-width: 10px;
    min-height: 10px;
    max-width: 10px;
    max-height: 10px;
}

.dot-online { background-color: #4ade80; }
.dot-offline { background-color: #ef4444; }
.dot-active { background-color: #4ade80; }
.dot-inactive { background-color: #6b7280; }
.dot-warning { background-color: #f59e0b; }

/* 表格 */
QTableWidget {
    background-color: #1e1e3a;
    border: none;
    gridline-color: #2a2a4a;
}

QTableWidget::item {
    padding: 6px;
    color: #d0d0e0;
}

QTableWidget::item:selected {
    background-color: #4a4a8a;
}

QHeaderView::section {
    background-color: #252542;
    padding: 6px;
    border: none;
    color: #a0a0c0;
}

/* 按钮 */
QPushButton {
    background-color: #4a4a8a;
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    color: white;
    font-weight: 500;
}

QPushButton:hover {
    background-color: #5a5a9a;
}

QPushButton.danger {
    background-color: #8a3a3a;
}

QPushButton.danger:hover {
    background-color: #9a4a4a;
}

/* 滚动条 */
QScrollArea {
    border: none;
    background: transparent;
}

QScrollBar:vertical {
    background-color: #1e1e3a;
    width: 8px;
    border-radius: 4px;
}

QScrollBar::handle:vertical {
    background-color: #4a4a6a;
    border-radius: 4px;
}

/* 进度条 */
QProgressBar {
    background-color: #1e1e3a;
    border-radius: 4px;
    height: 8px;
    text-align: center;
}

QProgressBar::chunk {
    background-color: #4ade80;
    border-radius: 4px;
}
"""


class MainWindow(QMainWindow):
    """视觉门控制系统主窗口"""

    def __init__(self, event_bus: EventBus, runtime_service=None):
        super().__init__()
        self.event_bus = event_bus
        self.runtime_service = runtime_service

        self.setWindowTitle("视觉门控制系统")
        self.setMinimumSize(1100, 750)
        self.setStyleSheet(STYLE)

        # 数据缓存
        self.stats_cache = {}
        self.results_cache = []  # 最多保留20条
        self.alarms_cache = []  # 最多保留10条
        self.device_cache = {
            "camera_a": {"online": False, "status": "离线"},
            "camera_b": {"online": False, "status": "离线"},
            "pe1": False,
            "pe2": False,
            "session_running": False,
            "conveyor_speed": 0
        }

        self.setup_ui()
        self.subscribe_events()

    def setup_ui(self):
        """设置 UI"""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)

        # ========== 顶部标题栏 ==========
        title_layout = QHBoxLayout()
        title_label = QLabel("📷 视觉门扫码控制系统")
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #ffffff;")
        title_layout.addWidget(title_label)
        title_layout.addStretch()

        # 系统状态指示
        self.sys_status = QLabel("● 运行中")
        self.sys_status.setStyleSheet("color: #4ade80; font-weight: bold;")
        title_layout.addWidget(self.sys_status)

        # 当前模式
        self.mode_label = QLabel("模式: LR")
        self.mode_label.setStyleSheet("color: #a0a0c0;")
        title_layout.addWidget(self.mode_label)

        main_layout.addLayout(title_layout)

        # ========== 第一行：统计卡片 ==========
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(15)

        self.ok_card = self._create_stat_card("今日 OK", "0", "✓", "#4ade80")
        self.ng_card = self._create_stat_card("今日 NG", "0", "✗", "#ef4444")
        self.active_card = self._create_stat_card("活动轨迹", "0", "📦", "#60a5fa")
        self.total_card = self._create_stat_card("累计轨迹", "0", "📋", "#a78bfa")

        stats_layout.addWidget(self.ok_card)
        stats_layout.addWidget(self.ng_card)
        stats_layout.addWidget(self.active_card)
        stats_layout.addWidget(self.total_card)
        main_layout.addLayout(stats_layout)

        # ========== 第二行：设备状态 ==========
        device_layout = QHBoxLayout()
        device_layout.setSpacing(15)

        # 相机状态
        camera_widget = self._create_camera_widget()
        device_layout.addWidget(camera_widget, 1)

        # 光电状态
        pe_widget = self._create_pe_widget()
        device_layout.addWidget(pe_widget, 1)

        # 传送带状态
        conveyor_widget = self._create_conveyor_widget()
        device_layout.addWidget(conveyor_widget, 1)

        main_layout.addLayout(device_layout)

        # ========== 第三行：扫码结果表格 ==========
        result_widget = QWidget()
        result_widget.setProperty("class", "card")
        result_layout = QVBoxLayout(result_widget)

        result_title = QLabel("📋 最近扫码结果")
        result_title.setProperty("class", "card-title")
        result_layout.addWidget(result_title)

        self.result_table = QTableWidget()
        self.result_table.setColumnCount(5)
        self.result_table.setHorizontalHeaderLabels(["时间", "轨迹 ID", "码值", "状态", "相机"])
        self.result_table.horizontalHeader().setStretchLastSection(True)
        self.result_table.setAlternatingRowColors(True)
        self.result_table.setMaximumHeight(250)
        result_layout.addWidget(self.result_table)

        main_layout.addWidget(result_widget)

        # ========== 第四行：报警区域 ==========
        alarm_widget = QWidget()
        alarm_widget.setProperty("class", "card")
        alarm_layout = QVBoxLayout(alarm_widget)

        alarm_title = QLabel("⚠️ 当前报警")
        alarm_title.setProperty("class", "card-title")
        alarm_layout.addWidget(alarm_title)

        self.alarm_scroll = QScrollArea()
        self.alarm_scroll.setWidgetResizable(True)
        self.alarm_scroll.setMaximumHeight(120)
        self.alarm_container = QWidget()
        self.alarm_container_layout = QVBoxLayout(self.alarm_container)
        self.alarm_container_layout.setSpacing(5)
        self.alarm_container_layout.addStretch()
        self.alarm_scroll.setWidget(self.alarm_container)
        alarm_layout.addWidget(self.alarm_scroll)

        main_layout.addWidget(alarm_widget)

        # ========== 底部按钮 ==========
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.reset_btn = QPushButton("重置统计")
        self.reset_btn.clicked.connect(self.on_reset_stats)
        btn_layout.addWidget(self.reset_btn)

        self.clear_alarm_btn = QPushButton("清除报警")
        self.clear_alarm_btn.setProperty("class", "danger")
        self.clear_alarm_btn.clicked.connect(self.on_clear_alarm)
        btn_layout.addWidget(self.clear_alarm_btn)

        main_layout.addLayout(btn_layout)

    def _create_stat_card(self, title: str, value: str, icon: str, color: str) -> QWidget:
        """创建统计卡片"""
        widget = QWidget()
        widget.setProperty("class", "card")
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(12, 10, 12, 10)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setSpacing(4)

        title_label = QLabel(title)
        title_label.setProperty("class", "stat-label")
        left_layout.addWidget(title_label)

        value_label = QLabel(value)
        value_label.setProperty("class", "stat-value")
        left_layout.addWidget(value_label)

        layout.addWidget(left)
        layout.addStretch()

        icon_label = QLabel(icon)
        icon_label.setStyleSheet(f"font-size: 28px; color: {color};")
        layout.addWidget(icon_label)

        widget.value_label = value_label
        return widget

    def _create_camera_widget(self) -> QWidget:
        """创建相机状态组件"""
        widget = QWidget()
        widget.setProperty("class", "card")
        layout = QVBoxLayout(widget)

        title = QLabel("📷 相机状态")
        title.setProperty("class", "card-title")
        layout.addWidget(title)

        # 相机 A
        cam_a_layout = QHBoxLayout()
        self.cam_a_dot = QLabel()
        self.cam_a_dot.setProperty("class", "dot dot-offline")
        self.cam_a_dot.setFixedSize(10, 10)
        cam_a_layout.addWidget(self.cam_a_dot)
        cam_a_layout.addWidget(QLabel("相机 A"))
        cam_a_layout.addStretch()
        self.cam_a_status = QLabel("离线")
        self.cam_a_status.setStyleSheet("color: #ef4444;")
        cam_a_layout.addWidget(self.cam_a_status)
        layout.addLayout(cam_a_layout)

        # 相机 B
        cam_b_layout = QHBoxLayout()
        self.cam_b_dot = QLabel()
        self.cam_b_dot.setProperty("class", "dot dot-offline")
        self.cam_b_dot.setFixedSize(10, 10)
        cam_b_layout.addWidget(self.cam_b_dot)
        cam_b_layout.addWidget(QLabel("相机 B"))
        cam_b_layout.addStretch()
        self.cam_b_status = QLabel("离线")
        self.cam_b_status.setStyleSheet("color: #ef4444;")
        cam_b_layout.addWidget(self.cam_b_status)
        layout.addLayout(cam_b_layout)

        # 扫码会话状态
        session_layout = QHBoxLayout()
        session_layout.addWidget(QLabel("扫码会话"))
        session_layout.addStretch()
        self.session_status = QLabel("空闲")
        self.session_status.setStyleSheet("color: #808080;")
        session_layout.addWidget(self.session_status)
        layout.addLayout(session_layout)

        return widget

    def _create_pe_widget(self) -> QWidget:
        """创建光电状态组件"""
        widget = QWidget()
        widget.setProperty("class", "card")
        layout = QVBoxLayout(widget)

        title = QLabel("🔌 光电传感器")
        title.setProperty("class", "card-title")
        layout.addWidget(title)

        # PE1
        pe1_layout = QHBoxLayout()
        self.pe1_dot = QLabel()
        self.pe1_dot.setProperty("class", "dot dot-inactive")
        self.pe1_dot.setFixedSize(10, 10)
        pe1_layout.addWidget(self.pe1_dot)
        pe1_layout.addWidget(QLabel("PE1 (入站)"))
        pe1_layout.addStretch()
        self.pe1_status = QLabel("空闲")
        self.pe1_status.setStyleSheet("color: #808080;")
        pe1_layout.addWidget(self.pe1_status)
        layout.addLayout(pe1_layout)

        # PE2
        pe2_layout = QHBoxLayout()
        self.pe2_dot = QLabel()
        self.pe2_dot.setProperty("class", "dot dot-inactive")
        self.pe2_dot.setFixedSize(10, 10)
        pe2_layout.addWidget(self.pe2_dot)
        pe2_layout.addWidget(QLabel("PE2 (读码)"))
        pe2_layout.addStretch()
        self.pe2_status = QLabel("空闲")
        self.pe2_status.setStyleSheet("color: #808080;")
        pe2_layout.addWidget(self.pe2_status)
        layout.addLayout(pe2_layout)

        return widget

    def _create_conveyor_widget(self) -> QWidget:
        """创建传送带状态组件"""
        widget = QWidget()
        widget.setProperty("class", "card")
        layout = QVBoxLayout(widget)

        title = QLabel("📊 运行参数")
        title.setProperty("class", "card-title")
        layout.addWidget(title)

        # 速度
        speed_layout = QHBoxLayout()
        speed_layout.addWidget(QLabel("输送速度"))
        speed_layout.addStretch()
        self.speed_value = QLabel("0.00")
        self.speed_value.setStyleSheet("font-size: 20px; font-weight: bold; color: #4ade80;")
        speed_layout.addWidget(self.speed_value)
        speed_layout.addWidget(QLabel("mm/s"))
        layout.addLayout(speed_layout)

        # 传送带动画
        self.conveyor_bar = QProgressBar()
        self.conveyor_bar.setRange(0, 100)
        self.conveyor_bar.setValue(0)
        self.conveyor_bar.setFormat("")
        self.conveyor_bar.setFixedHeight(8)
        layout.addWidget(self.conveyor_bar)

        # 当前位置
        self.position_label = QLabel("无活动鞋盒")
        self.position_label.setStyleSheet("color: #8080a0; font-size: 11px;")
        self.position_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.position_label)

        return widget

    def subscribe_events(self):
        """订阅事件总线"""
        # 订阅 UI 更新事件
        self.event_bus.subscribe(EventType.UI_UPDATE, self._on_ui_update)

    @asyncSlot()
    async def _on_ui_update(self, event: AppEvent):
        """处理 UI 更新事件"""
        payload = event.payload
        update_type = payload.get("type")

        if update_type == "result":
            # 扫码结果更新
            self._add_result(payload)
            # 更新统计
            stats = payload.get("stats", {})
            self._update_stats(stats)

        elif update_type == "track_created":
            # 轨迹创建
            track = payload.get("track", {})
            self._update_active_count()

        elif update_type == "window_opened":
            # 窗口打开
            self._update_active_count()

        elif update_type == "camera_status":
            # 相机状态
            camera_id = payload.get("camera_id")
            status = payload.get("status")
            self._update_camera_status(camera_id, status)

        elif update_type == "device_fault":
            # 设备故障
            device_id = payload.get("device_id")
            message = payload.get("message")
            self._add_alarm(f"DEVICE_FAULT_{device_id}", message, "ERROR")

        elif update_type == "alarm":
            # 报警
            alarm = payload.get("alarm", {})
            self._add_alarm(
                alarm.get("code", "UNKNOWN"),
                alarm.get("message", ""),
                alarm.get("level", "ERROR")
            )

    def _update_stats(self, stats: dict):
        """更新统计显示"""
        self.ok_card.value_label.setText(str(stats.get("ok_count", 0)))
        self.ng_card.value_label.setText(str(stats.get("ng_count", 0)))
        self.total_card.value_label.setText(str(stats.get("total_tracks", 0)))
        self.active_card.value_label.setText(str(stats.get("active_tracks", 0)))

        # 更新扫码会话状态
        active = stats.get("active_tracks", 0)
        if active > 0:
            self.session_status.setText("运行中")
            self.session_status.setStyleSheet("color: #4ade80;")
        else:
            self.session_status.setText("空闲")
            self.session_status.setStyleSheet("color: #808080;")

        self.stats_cache = stats

    def _update_active_count(self):
        """更新活动轨迹数量"""
        if self.runtime_service:
            active = self.runtime_service.track_manager.active_count
            self.active_card.value_label.setText(str(active))

    def _update_camera_status(self, camera_id: str, status: str):
        """更新相机状态"""
        is_online = status == "ONLINE" or status == "online"

        if camera_id == 1 or camera_id == "1" or camera_id == "CAM1":
            if is_online:
                self.cam_a_dot.setProperty("class", "dot dot-online")
                self.cam_a_status.setText("在线")
                self.cam_a_status.setStyleSheet("color: #4ade80;")
            else:
                self.cam_a_dot.setProperty("class", "dot dot-offline")
                self.cam_a_status.setText("离线")
                self.cam_a_status.setStyleSheet("color: #ef4444;")

        elif camera_id == 2 or camera_id == "2" or camera_id == "CAM2":
            if is_online:
                self.cam_b_dot.setProperty("class", "dot dot-online")
                self.cam_b_status.setText("在线")
                self.cam_b_status.setStyleSheet("color: #4ade80;")
            else:
                self.cam_b_dot.setProperty("class", "dot dot-offline")
                self.cam_b_status.setText("离线")
                self.cam_b_status.setStyleSheet("color: #ef4444;")

        # 刷新样式
        for w in [self.cam_a_dot, self.cam_b_dot]:
            w.style().unpolish(w)
            w.style().polish(w)

    def _add_result(self, payload: dict):
        """添加扫码结果"""
        result = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "track_id": payload.get("track_id", "")[-12:],
            "code": payload.get("code", "---"),
            "status": payload.get("status", "UNKNOWN"),
            "camera": payload.get("camera_id", "?")
        }

        # 添加到缓存头部
        self.results_cache.insert(0, result)
        # 保留最多20条
        self.results_cache = self.results_cache[:20]

        self._refresh_result_table()

    def _refresh_result_table(self):
        """刷新结果表格"""
        self.result_table.setRowCount(len(self.results_cache))

        for i, r in enumerate(self.results_cache):
            # 时间
            self.result_table.setItem(i, 0, QTableWidgetItem(r["time"]))
            # 轨迹 ID
            self.result_table.setItem(i, 1, QTableWidgetItem(r["track_id"]))
            # 码值
            self.result_table.setItem(i, 2, QTableWidgetItem(r["code"]))
            # 状态
            status_item = QTableWidgetItem(r["status"])
            color = {
                "OK": "#4ade80",
                "NO_READ": "#ef4444",
                "AMBIGUOUS": "#f59e0b",
                "TIMEOUT": "#f59e0b",
                "FAULT": "#ef4444"
            }.get(r["status"], "#808080")
            status_item.setForeground(QColor(color))
            self.result_table.setItem(i, 3, status_item)
            # 相机
            self.result_table.setItem(i, 4, QTableWidgetItem(str(r["camera"])))

        # 调整列宽
        self.result_table.resizeColumnsToContents()

    def _add_alarm(self, code: str, message: str, level: str = "ERROR"):
        """添加报警"""
        alarm_widget = QWidget()
        alarm_layout = QHBoxLayout(alarm_widget)
        alarm_layout.setContentsMargins(5, 3, 5, 3)

        # 级别颜色
        color = {"ERROR": "#ef4444", "WARN": "#f59e0b", "INFO": "#4ade80"}.get(level, "#808080")
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background-color: {color}; border-radius: 4px;")
        alarm_layout.addWidget(dot)

        # 报警码
        code_label = QLabel(f"[{code}]")
        code_label.setStyleSheet("font-weight: bold;")
        alarm_layout.addWidget(code_label)

        # 消息
        msg_label = QLabel(message[:50])
        msg_label.setWordWrap(True)
        alarm_layout.addWidget(msg_label, 1)

        # 时间
        time_label = QLabel(datetime.now().strftime("%H:%M:%S"))
        time_label.setStyleSheet("color: #808080; font-size: 11px;")
        alarm_layout.addWidget(time_label)

        # 插入到顶部
        self.alarm_container_layout.insertWidget(0, alarm_widget)

        # 限制最多显示 10 条
        while self.alarm_container_layout.count() > 11:
            item = self.alarm_container_layout.takeAt(10)
            if item.widget():
                item.widget().deleteLater()

    def update_conveyor_speed(self, speed_mm_s: float):
        """更新传送带速度"""
        self.speed_value.setText(f"{speed_mm_s:.1f}")

        # 动画效果：速度越快进度条越长
        speed_percent = min(100, int(speed_mm_s / 1000 * 100))
        self.conveyor_bar.setValue(speed_percent)

    def update_pe_status(self, pe1: bool, pe2: bool):
        """更新光电状态"""
        if pe1:
            self.pe1_dot.setProperty("class", "dot dot-active")
            self.pe1_status.setText("触发")
            self.pe1_status.setStyleSheet("color: #4ade80;")
        else:
            self.pe1_dot.setProperty("class", "dot dot-inactive")
            self.pe1_status.setText("空闲")
            self.pe1_status.setStyleSheet("color: #808080;")

        if pe2:
            self.pe2_dot.setProperty("class", "dot dot-active")
            self.pe2_status.setText("触发")
            self.pe2_status.setStyleSheet("color: #4ade80;")
        else:
            self.pe2_dot.setProperty("class", "dot dot-inactive")
            self.pe2_status.setText("空闲")
            self.pe2_status.setStyleSheet("color: #808080;")

        # 刷新样式
        for w in [self.pe1_dot, self.pe2_dot]:
            w.style().unpolish(w)
            w.style().polish(w)

    def on_reset_stats(self):
        """重置统计"""
        reply = QMessageBox.question(self, "确认", "确定重置统计数据吗？")
        if reply == QMessageBox.Yes and self.runtime_service:
            self.runtime_service.stats = {
                "total_tracks": 0,
                "ok_count": 0,
                "ng_count": 0,
                "ambiguous_count": 0,
                "timeout_count": 0,
                "fault_count": 0
            }
            self.ok_card.value_label.setText("0")
            self.ng_card.value_label.setText("0")
            self.total_card.value_label.setText("0")

    def on_clear_alarm(self):
        """清除报警"""
        reply = QMessageBox.question(self, "确认", "确定清除所有报警吗？")
        if reply == QMessageBox.Yes:
            while self.alarm_container_layout.count() > 1:
                item = self.alarm_container_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()