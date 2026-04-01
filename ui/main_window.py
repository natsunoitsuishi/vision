import tkinter as tk
from tkinter import ttk
import threading
import time
import asyncio
from typing import Optional

from infra import get_logger
from domain.enums import EventType
from domain.models import AppEvent

logger = get_logger(__name__)


class MainWindow:
    """鞋盒队列监控主窗口 - 在独立线程中运行"""

    def __init__(self, event_bus=None):
        self._root = None
        self._running = False
        self._thread = None
        self._archive_service = None
        self._event_bus = event_bus
        self._update_interval_ms = 500  # 定时刷新间隔（作为备用）

        # UI 组件引用
        self.active_label = None
        self.finished_label = None
        self.tree = None
        self.head_label = None
        self.tail_label = None
        self.timestamp_label = None
        self.logger = get_logger("ui")

        # 字体设置
        self.title_font = ('Arial', 16, 'bold')
        self.content_font = ('Courier', 10)

    def set_archive_service(self, archive_service):
        """设置 ArchiveService 引用"""
        self._archive_service = archive_service
        self.logger.info("ArchiveService 已设置到 UI")

    def start(self):
        """启动 UI"""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_ui, daemon=True)
        self._thread.start()
        self.logger.info("UI 线程已启动")

    def stop(self):
        """停止 UI"""
        self._running = False
        if self._root:
            self._root.quit()
        self.logger.info("UI 已停止")

    def _run_ui(self):
        """运行 UI 主循环"""
        self._root = tk.Tk()
        self._root.title("Vision Gate - 鞋盒队列监控")
        self._root.geometry("1000x600")
        self._root.configure(bg='#2c3e50')
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._create_widgets()

        # 启动定时刷新循环（作为备用，确保 UI 会更新）
        self._update_loop()

        # 如果有事件总线，订阅 UI 更新事件
        if self._event_bus:
            self._subscribe_to_events()

        self._root.mainloop()

    def _subscribe_to_events(self):
        """订阅事件总线中的 UI 更新事件"""

        # 注意：需要在主线程中处理事件回调
        # 使用 after 方法将事件处理调度到 Tkinter 主线程
        def on_ui_update(event: AppEvent):
            # 在 Tkinter 主线程中刷新 UI
            if self._root:
                self._root.after(0, self._refresh_display)

        # 订阅 UI_UPDATE 事件
        self._event_bus.subscribe(EventType.UI_UPDATE, on_ui_update)
        self.logger.info("已订阅 UI_UPDATE 事件")

    def _on_close(self):
        """窗口关闭回调"""
        self._running = False
        if self._root:
            self._root.quit()
        self.logger.info("UI 窗口已关闭")

    def _create_widgets(self):
        """创建界面组件"""
        # 主框架
        main_frame = ttk.Frame(self._root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        self._root.columnconfigure(0, weight=1)
        self._root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(2, weight=1)

        # 标题
        title_label = tk.Label(
            main_frame, text="📦 鞋盒队列状态监控",
            font=self.title_font, bg='#2c3e50', fg='#ecf0f1'
        )
        title_label.grid(row=0, column=0, pady=(0, 10))

        # 统计信息框架
        stats_frame = ttk.LabelFrame(main_frame, text="统计信息", padding="10")
        stats_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        stats_frame.columnconfigure(0, weight=1)
        stats_frame.columnconfigure(1, weight=1)

        self.active_label = tk.Label(
            stats_frame, text="活动鞋盒: 0", font=self.content_font,
            bg='#ecf0f1', fg='#2c3e50', relief=tk.RIDGE, padx=10, pady=5
        )
        self.active_label.grid(row=0, column=0, padx=5, pady=5, sticky=(tk.W, tk.E))

        self.finished_label = tk.Label(
            stats_frame, text="已完成: 0", font=self.content_font,
            bg='#ecf0f1', fg='#2c3e50', relief=tk.RIDGE, padx=10, pady=5
        )
        self.finished_label.grid(row=0, column=1, padx=5, pady=5, sticky=(tk.W, tk.E))

        # 队列显示区域
        queue_frame = ttk.LabelFrame(main_frame, text="鞋盒队列详情", padding="10")
        queue_frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 10))
        queue_frame.columnconfigure(0, weight=1)
        queue_frame.rowconfigure(0, weight=1)

        columns = ('position', 'track_id', 'speed', 'status', 'target_divert')
        self.tree = ttk.Treeview(queue_frame, columns=columns, show='headings', height=12)

        self.tree.heading('position', text='位置(mm)')
        self.tree.heading('track_id', text='轨迹ID')
        self.tree.heading('speed', text='速度(mm/s)')
        self.tree.heading('status', text='状态')
        self.tree.heading('target_divert', text='目标摆轮机')

        self.tree.column('position', width=100, anchor='center')
        self.tree.column('track_id', width=280, anchor='center')
        self.tree.column('speed', width=100, anchor='center')
        self.tree.column('status', width=100, anchor='center')
        self.tree.column('target_divert', width=100, anchor='center')

        scrollbar = ttk.Scrollbar(queue_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))

        # 头尾盒信息
        info_frame = ttk.LabelFrame(main_frame, text="队列头尾", padding="10")
        info_frame.grid(row=3, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        info_frame.columnconfigure(0, weight=1)
        info_frame.columnconfigure(1, weight=1)

        self.head_label = tk.Label(
            info_frame, text="头盒: --", font=self.content_font,
            bg='#ecf0f1', fg='#2c3e50', relief=tk.RIDGE, padx=10, pady=5
        )
        self.head_label.grid(row=0, column=0, padx=5, pady=5, sticky=(tk.W, tk.E))

        self.tail_label = tk.Label(
            info_frame, text="尾盒: --", font=self.content_font,
            bg='#ecf0f1', fg='#2c3e50', relief=tk.RIDGE, padx=10, pady=5
        )
        self.tail_label.grid(row=0, column=1, padx=5, pady=5, sticky=(tk.W, tk.E))

        # 时间戳
        self.timestamp_label = tk.Label(
            main_frame, text="最后更新: --",
            font=('Arial', 8), bg='#2c3e50', fg='#95a5a6'
        )
        self.timestamp_label.grid(row=4, column=0, pady=(5, 0))

        self.logger.info("UI 组件创建完成")

    def _update_loop(self):
        """定时刷新循环（备用，确保 UI 会更新）"""
        if not self._running:
            return

        try:
            self._refresh_display()
        except Exception as e:
            self.logger.error(f"定时刷新 UI 失败: {e}")

        if self._root:
            self._root.after(self._update_interval_ms, self._update_loop)

    def _refresh_display(self):
        """刷新显示内容（从 archive_service 获取数据）"""
        if not self._archive_service:
            if self.timestamp_label:
                self.timestamp_label.config(text="等待 ArchiveService...")
            return

        try:
            # 获取队列状态
            queue_status = self._archive_service.get_queue_status()

            # 更新统计信息
            if self.active_label:
                self.active_label.config(text=f"活动鞋盒: {queue_status['active_count']}")
            if self.finished_label:
                self.finished_label.config(text=f"已完成: {queue_status['finished_count']}")

            # 更新表格
            if self.tree:
                for item in self.tree.get_children():
                    self.tree.delete(item)

                for item in queue_status['queue']:
                    self.tree.insert('', 'end', values=(
                        f"{item['position']:.1f}",
                        item['track_id'],
                        f"{item['speed']:.1f}",
                        item['status'],
                        item.get('target_divert', '-') or '-'
                    ))

            # 更新头盒信息
            if self.head_label and queue_status.get('head_box'):
                self.head_label.config(
                    text=f"头盒: {queue_status['head_box']['track_id']} @ {queue_status['head_box']['position']:.1f}mm"
                )
            elif self.head_label:
                self.head_label.config(text="头盒: --")

            # 更新尾盒信息
            if self.tail_label and queue_status.get('tail_box'):
                self.tail_label.config(
                    text=f"尾盒: {queue_status['tail_box']['track_id']} @ {queue_status['tail_box']['position']:.1f}mm"
                )
            elif self.tail_label:
                self.tail_label.config(text="尾盒: --")

            # 更新时间戳
            if self.timestamp_label:
                self.timestamp_label.config(text=f"最后更新: {time.strftime('%H:%M:%S')}")

            self.logger.debug(f"UI 刷新完成: 活动={queue_status['active_count']}")

        except Exception as e:
            self.logger.error(f"刷新显示失败: {e}")
            if self.timestamp_label:
                self.timestamp_label.config(text=f"错误: {str(e)[:50]}")