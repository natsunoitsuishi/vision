import asyncio
import logging
from time import sleep
from typing import Dict, List, Callable, Awaitable, Any, Optional, Set
from collections import defaultdict
from datetime import datetime
import traceback

from domain.enums import EventType
from domain.models import AppEvent

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

class EventBus:
    """
    轻量级内存事件总线

    基于 asyncio.Queue 实现事件的异步分发处理。
    支持事件订阅、发布、通配符监听、事件过滤等功能。

    设计特点：
    - 单队列串行处理，避免并发竞争
    - 支持同步和异步两种发布方式
    - 内置监控和统计功能
    - 优雅的错误隔离（单个处理器异常不影响其他处理器）
    """

    def __init__(self, max_queue_size: int = 1000, processor_name: str = "EventBus"):
        """
        初始化事件总线

        Args:
            max_queue_size: 事件队列最大长度，超过则丢弃新事件
            processor_name: 处理器名称，用于日志和监控
        """
        self._max_queue_size = max_queue_size
        self._processor_name = processor_name

        # 订阅者存储：事件类型 -> 处理器列表
        self._subscribers: Dict[EventType, List[Callable[[AppEvent], Awaitable[Any]]]] = defaultdict(list)

        # 通配符订阅者：监听所有事件
        self._wildcard_subscribers: List[Callable[[AppEvent], Awaitable[Any]]] = []

        # 已注册的处理器集合（用于去重）
        self._handlers: Set[Callable] = set()

        # 事件队列（用于异步处理）
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self._processor_task: Optional[asyncio.Task] = None
        self._is_running = False

        # 监控统计
        self._stats = {
            "published": 0,  # 发布事件总数
            "processed": 0,  # 成功处理事件数
            "dropped": 0,  # 丢弃事件数（队列满）
            "errors": 0,  # 处理错误数
            "start_time": None,  # 启动时间
            "last_event_time": None,  # 最后事件时间
            "peak_queue_size": 0  # 峰值队列长度
        }

        logger.info(f"✅ 事件总线 [{processor_name}] 已创建，队列容量: {max_queue_size}")

    # =========================================================================
    # 订阅管理
    # =========================================================================

    def subscribe(self, event_type: EventType, handler: Callable[[AppEvent], Awaitable[Any]]) -> bool:
        """
        订阅特定类型的事件

        Args:
            event_type: 要订阅的事件类型
            handler: 异步处理函数（必须为async函数）

        Returns:
            bool: 是否成功订阅（False表示已存在）
        """
        # 检查处理器是否已注册
        handler_id = self._get_handler_id(handler)
        if handler_id in self._handlers:
            logger.warning(f"⚠️ 处理器 {handler.__name__} 已存在，跳过订阅")
            return False

        # 添加到订阅列表
        if handler not in self._subscribers[event_type]:
            self._subscribers[event_type].append(handler)
            self._handlers.add(handler_id)
            logger.info(f"📝 订阅事件 [{event_type.value}]: {handler.__name__}")
            return True

        return False

    def subscribe_all(self, handler: Callable[[AppEvent], Awaitable[Any]]) -> bool:
        """
        订阅所有事件（通配符）

        Args:
            handler: 异步处理函数

        Returns:
            bool: 是否成功订阅
        """
        handler_id = self._get_handler_id(handler)
        if handler_id in self._handlers:
            logger.warning(f"⚠️ 通配符处理器 {handler.__name__} 已存在")
            return False

        if handler not in self._wildcard_subscribers:
            self._wildcard_subscribers.append(handler)
            self._handlers.add(handler_id)
            logger.debug(f"📝 订阅所有事件: {handler.__name__}")
            return True

        return False

    def subscribe_batch(self, subscriptions: Dict[EventType, List[Callable]]) -> Dict[EventType, int]:
        """
        批量订阅事件

        Args:
            subscriptions: 事件类型到处理器列表的映射

        Returns:
            Dict[EventType, int]: 每个事件类型成功订阅的数量
        """
        results = {}
        for event_type, handlers in subscriptions.items():
            success_count = 0
            for handler in handlers:
                if self.subscribe(event_type, handler):
                    success_count += 1
            results[event_type] = success_count

        logger.info(f"📦 批量订阅完成: {results}")
        return results

    def unsubscribe(self, event_type: EventType, handler: Callable[[AppEvent], Awaitable[Any]]) -> bool:
        """
        取消订阅

        Returns:
            bool: 是否成功取消
        """
        if handler in self._subscribers[event_type]:
            self._subscribers[event_type].remove(handler)
            handler_id = self._get_handler_id(handler)
            self._handlers.discard(handler_id)
            logger.debug(f"🗑️ 取消订阅 [{event_type.value}]: {handler.__name__}")
            return True
        return False

    def unsubscribe_all(self, handler: Callable[[AppEvent], Awaitable[Any]]) -> bool:
        """
        取消所有订阅

        Returns:
            bool: 是否找到并取消
        """
        found = False

        # 从通配符列表中移除
        if handler in self._wildcard_subscribers:
            self._wildcard_subscribers.remove(handler)
            found = True

        # 从所有特定类型订阅中移除
        for handlers in self._subscribers.values():
            if handler in handlers:
                handlers.remove(handler)
                found = True

        if found:
            handler_id = self._get_handler_id(handler)
            self._handlers.discard(handler_id)
            logger.debug(f"🗑️ 取消所有订阅: {handler.__name__}")

        return found

    def clear(self):
        """清除所有订阅"""
        self._subscribers.clear()
        self._wildcard_subscribers.clear()
        self._handlers.clear()
        logger.info("🧹 已清除所有事件订阅")

    # =========================================================================
    # 事件发布
    # =========================================================================

    async def publish(self, event: AppEvent) -> bool:
        """
        发布事件到队列（异步处理）

        将事件放入队列，由后台处理器异步处理。
        如果队列满，事件会被丢弃并记录统计。

        Args:
            event: 要发布的事件

        Returns:
            bool: 是否成功放入队列
        """
        try:
            # 尝试放入队列，如果队列满则丢弃
            self._queue.put_nowait(event)

            # 更新统计
            self._stats["published"] += 1
            self._stats["last_event_time"] = datetime.now().timestamp()
            self._stats["peak_queue_size"] = max(self._stats["peak_queue_size"], self._queue.qsize())

            logger.info(f"📤 事件入队 [{self._queue.qsize()}/{self._max_queue_size}]: {event}")
            return True

        except asyncio.QueueFull:
            self._stats["dropped"] += 1
            logger.warning(f"⚠️ 事件队列已满，丢弃事件: {event}")
            return False

    async def publish_nowait(self, event: AppEvent) -> int:
        """
        同步发布事件（立即处理，不经过队列）

        适用于优先级高或需要立即响应的事件。

        Args:
            event: 要发布的事件

        Returns:
            int: 成功处理的处理器数量
        """
        self._stats["published"] += 1
        self._stats["last_event_time"] = datetime.now().timestamp()

        return await self._dispatch_event(event)

    def emit(self, event_type: EventType, source: str, payload: dict = None) -> bool:
        """
        便捷方法：创建并发布事件（异步）

        Args:
            event_type: 事件类型
            source: 事件来源
            payload: 事件数据

        Returns:
            bool: 是否成功放入队列
        """
        event = AppEvent.create(
            event_type=event_type,
            source=source,
            payload=payload or {}
        )
        # 创建异步任务，不等待
        asyncio.create_task(self.publish(event))
        return True

    def emit_sync(self, event_type: EventType, source: str, payload: dict = None) -> int:
        """
        同步触发事件（立即处理）

        Args:
            event_type: 事件类型
            source: 事件来源
            payload: 事件数据

        Returns:
            int: 成功处理的处理器数量
        """
        event = AppEvent.create(
            event_type=event_type,
            source=source,
            payload=payload or {}
        )

        # 立即分发
        return asyncio.create_task(self.publish_nowait(event))

    # =========================================================================
    # 事件分发
    # =========================================================================

    async def _dispatch_event(self, event: AppEvent) -> int:
        """
        将事件分发给所有订阅者

        Args:
            event: 要分发的事件

        Returns:
            int: 成功处理的处理器数量
        """
        handled_count = 0
        errors = []

        # 1. 分发给特定类型的订阅者
        for handler in self._subscribers.get(event.event_type, []):
            try:
                await handler(event)
                handled_count += 1
            except asyncio.CancelledError:
                raise
            except Exception as e:
                errors.append(f"{handler.__name__}: {str(e)}")
                self._stats["errors"] += 1
                logger.error(f"❌ 事件处理失败 [{handler.__name__}]: {e}")
                logger.debug(traceback.format_exc())

        # 2. 分发给通配符订阅者
        for handler in self._wildcard_subscribers:
            try:
                await handler(event)
                handled_count += 1
            except asyncio.CancelledError:
                raise
            except Exception as e:
                errors.append(f"{handler.__name__}: {str(e)}")
                self._stats["errors"] += 1
                logger.error(f"❌ 通配符处理失败 [{handler.__name__}]: {e}")
                logger.debug(traceback.format_exc())

        if errors:
            logger.warning(f"事件 {event} 处理完成，{len(errors)} 个错误")

        return handled_count

    async def _processor_loop(self):
        """后台任务：事件处理主循环"""
        logger.info(f"🔄 事件处理器 [{self._processor_name}] 已启动")

        while self._is_running:
            try:
                # 从队列获取事件（阻塞）
                event = await self._queue.get()

                # 分发事件
                handler_count = await self._dispatch_event(event)

                # 更新统计
                self._stats["processed"] += 1

                logger.debug(f"✅ 事件处理完成: {event}, 处理器数: {handler_count}")

                # 标记任务完成
                self._queue.task_done()

            except asyncio.CancelledError:
                logger.info(f"🛑 事件处理器 [{self._processor_name}] 被取消")
                break
            except Exception as e:
                logger.error(f"❌ 事件处理循环异常: {e}", exc_info=True)
                self._stats["errors"] += 1
                await asyncio.sleep(0.1)  # 防止疯狂报错

    # =========================================================================
    # 生命周期管理
    # =========================================================================

    def start(self):
        """启动事件总线"""
        if self._is_running:
            logger.warning("事件总线已在运行中")
            return

        self._is_running = True
        self._stats["start_time"] = datetime.now().timestamp()
        self._processor_task = asyncio.create_task(
            self._processor_loop(),
            name=f"{self._processor_name}-Processor"
        )
        logger.info(f"✅ 事件总线 [{self._processor_name}] 已启动")

    async def stop(self, timeout: float = 5.0):
        """
        停止事件总线

        Args:
            timeout: 等待队列处理完成的超时时间（秒）
        """
        logger.info(f"🛑 正在停止事件总线 [{self._processor_name}]...")
        self._is_running = False

        if self._processor_task:
            # 等待队列处理完成（带超时）
            try:
                await asyncio.wait_for(self._queue.join(), timeout=timeout)
                logger.info(f"✅ 事件队列已清空，共处理 {self._stats['processed']} 个事件")
            except asyncio.TimeoutError:
                logger.warning(f"⚠️ 等待队列处理超时，剩余 {self._queue.qsize()} 个事件")

            # 取消处理器任务
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass
            self._processor_task = None

        logger.info(f"✅ 事件总线 [{self._processor_name}] 已停止")
        self.print_stats()

    # =========================================================================
    # 查询和监控
    # =========================================================================

    def get_stats(self) -> dict:
        """获取统计信息"""
        queue_size = self._queue.qsize()
        return {
            "published": self._stats["published"],
            "processed": self._stats["processed"],
            "dropped": self._stats["dropped"],
            "errors": self._stats["errors"],
            "queue_size": queue_size,
            "peak_queue_size": self._stats["peak_queue_size"],
            "uptime": (datetime.now().timestamp() - self._stats["start_time"]) if self._stats["start_time"] else 0,
            "last_event_ago": (datetime.now().timestamp() - self._stats["last_event_time"]) if self._stats[
                "last_event_time"] else None,
            "subscribers": {
                "specific": sum(len(h) for h in self._subscribers.values()),
                "wildcard": len(self._wildcard_subscribers),
                "total_handlers": len(self._handlers)
            }
        }

    def print_stats(self):
        """打印统计信息"""
        stats = self.get_stats()
        logger.info(f"📊 事件总线 [{self._processor_name}] 统计:")
        logger.info(f"   - 发布: {stats['published']}")
        logger.info(f"   - 处理: {stats['processed']}")
        logger.info(f"   - 丢弃: {stats['dropped']}")
        logger.info(f"   - 错误: {stats['errors']}")
        logger.info(f"   - 队列: {stats['queue_size']}/{self._max_queue_size} (峰值: {stats['peak_queue_size']})")
        logger.info(f"   - 订阅者: {stats['subscribers']}")
        logger.info(f"   - 运行时间: {stats['uptime']:.1f}秒")

    def list_subscribers(self, event_type: Optional[EventType] = None) -> Dict[str, List[str]]:
        """
        列出所有订阅者（用于调试）

        Args:
            event_type: 指定事件类型，None则返回所有

        Returns:
            Dict: 事件类型 -> 处理器名称列表
        """
        if event_type:
            return {
                event_type.value: [h.__name__ for h in self._subscribers.get(event_type, [])]
            }

        result = {}
        for et, handlers in self._subscribers.items():
            result[et.value] = [h.__name__ for h in handlers]

        if self._wildcard_subscribers:
            result["*WILDCARD*"] = [h.__name__ for h in self._wildcard_subscribers]

        return result

    def is_busy(self) -> bool:
        """判断事件总线是否繁忙（队列占用率 > 80%）"""
        return self._queue.qsize() > (self._max_queue_size * 0.8)

    # =========================================================================
    # 内部工具方法
    # =========================================================================

    @staticmethod
    def _get_handler_id(handler: Callable) -> str:
        """获取处理器的唯一标识"""
        return f"{handler.__module__}.{handler.__name__}"


# =========================================================================
# 装饰器：用于自动注册事件处理器
# =========================================================================

def event_listener(event_type: EventType):
    """
    事件监听器装饰器

    用于标记一个方法为事件处理器，便于自动注册。

    Usage:
        class MyService:
            def __init__(self, event_bus):
                event_bus.subscribe(EventType.PE_RISE, self.on_pe_rise)

            @event_listener(EventType.PE_RISE)
            async def on_pe_rise(self, event: AppEvent):
                print(f"收到事件: {event}")
    """

    def decorator(func):
        func._event_listener = event_type
        return func

    return decorator


# =========================================================================
# 快捷函数：创建带监控的事件总线
# =========================================================================

def create_event_bus(name: str = "EventBus", max_queue_size: int = 1000) -> EventBus:
    """
    创建并启动事件总线的快捷函数

    Args:
        name: 事件总线名称
        max_queue_size: 队列最大长度

    Returns:
        EventBus: 已启动的事件总线实例
    """
    bus = EventBus(max_queue_size=max_queue_size, processor_name=name)
    bus.start()
    return bus