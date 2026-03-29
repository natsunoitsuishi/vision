# config/manager.py
import os
import asyncio
import yaml
from typing import Any, Dict, Optional, Callable
from pathlib import Path
import logging
from copy import deepcopy

import scripts.util
from scripts.util import get_project_config_path


class ConfigError(Exception):
    """配置错误异常"""
    pass


class ConfigManager:
    """配置管理器 - 单例模式，支持异步加载"""

    _instance: Optional['ConfigManager'] = None
    _initialized: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self._config: Dict[str, Any] = {}
        self._config_path: Optional[Path] = None
        self._watch_enabled = False
        self._watch_task: Optional[asyncio.Task] = None
        self._on_change_callback: Optional[Callable] = None
        self._logger = logging.getLogger(__name__)
        self._load_lock = asyncio.Lock()  # 防止并发加载

    async def load(self, config_path: str = scripts.util.get_project_config_path(),
                   watch: bool = False) -> None:
        """
        异步加载配置文件
        """
        async with self._load_lock:
            self._config_path = Path(config_path)

            if not self._config_path.exists():
                raise ConfigError(f"配置文件不存在: {config_path}")

            # 加载配置
            await self._load_from_file()

            # 加载环境变量覆盖（如果有）
            self._load_from_env()

            self._logger.info(f"配置已加载: {config_path}")

            # 如果开启监听，启动文件监控
            if watch:
                await self._start_watching()

    async def reload(self) -> Dict[str, Any]:
        """异步重新加载配置"""
        if not self._config_path:
            raise ConfigError("没有加载过配置文件")

        async with self._load_lock:
            old_config = deepcopy(self._config)
            await self._load_from_file()
            self._load_from_env()

            self._logger.info("配置已重新加载")

            # 返回变更的配置项（可用于热更新）
            return self._get_changes(old_config, self._config)

    def get_config(self) -> Dict[str, Any]:
        """获取完整配置（同步）"""
        return deepcopy(self._config)

    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置项，支持点号访问嵌套
        """
        try:
            value = self._config
            for part in key.split('.'):
                if part.isdigit():  # 数组索引
                    value = value[int(part)]
                else:
                    value = value[part]
            return value
        except (KeyError, IndexError, TypeError):
            return default

    def set(self, key: str, value: Any) -> None:
        """
        设置配置项，支持点号访问嵌套

        Examples:
            config.set("database.path", "new_path")
        """
        parts = key.split('.')
        target = self._config

        # 导航到最后一个父节点
        for part in parts[:-1]:
            if part.isdigit():
                idx = int(part)
                # 确保列表有足够的长度
                if not isinstance(target, list):
                    # 如果目标不是列表，无法处理数字索引
                    raise ConfigError(f"无法使用数字索引 '{part}'，因为目标不是列表")
                while len(target) <= idx:
                    target.append({})
                target = target[idx]
            else:
                if part not in target:
                    target[part] = {}
                elif not isinstance(target[part], dict):
                    # 如果存在但不是字典，覆盖为字典
                    target[part] = {}
                target = target[part]

        # 设置值
        last_part = parts[-1]
        if last_part.isdigit():
            idx = int(last_part)
            if not isinstance(target, list):
                raise ConfigError(f"无法使用数字索引 '{last_part}'，因为目标不是列表")
            while len(target) <= idx:
                target.append(None)
            target[idx] = value
        else:
            target[last_part] = value

    def update(self, updates: Dict[str, Any]) -> None:
        """批量更新配置"""
        self._deep_update(self._config, updates)

    async def _load_from_file(self) -> None:
        """异步从文件加载配置"""
        try:
            # 使用 asyncio 的线程池执行文件读取（避免阻塞事件循环）
            loop = asyncio.get_event_loop()

            def read_yaml():
                with open(self._config_path, 'r', encoding='utf-8') as f:
                    return yaml.safe_load(f) or {}

            file_config = await loop.run_in_executor(None, read_yaml)

            # 如果是第一次加载，直接赋值
            if not self._config:
                self._config = file_config
            else:
                # 否则深度合并
                self._deep_update(self._config, file_config)

        except yaml.YAMLError as e:
            raise ConfigError(f"YAML解析错误: {e}")
        except Exception as e:
            raise ConfigError(f"读取配置文件失败: {e}")

    def _load_from_env(self) -> None:
        """从环境变量加载配置（用于覆盖）"""
        # 环境变量格式：VISION_GATE__DATABASE__PATH=/custom/path
        prefix = "VISION_GATE__"

        for env_key, env_value in os.environ.items():
            if not env_key.startswith(prefix):
                continue

            # 解析配置路径
            config_key = env_key[len(prefix):].lower().replace('__', '.')

            # 尝试转换类型
            if env_value.lower() in ('true', 'false'):
                typed_value = env_value.lower() == 'true'
            elif env_value.isdigit():
                typed_value = int(env_value)
            elif self._is_float(env_value):
                typed_value = float(env_value)
            else:
                typed_value = env_value

            # 设置配置
            try:
                self.set(config_key, typed_value)
                self._logger.debug(f"环境变量覆盖: {config_key}={typed_value}")
            except ConfigError as e:
                self._logger.warning(f"环境变量覆盖失败 {config_key}: {e}")

    def _deep_update(self, target: Dict, source: Dict) -> None:
        """深度更新字典"""
        for key, value in source.items():
            if isinstance(value, dict) and key in target and isinstance(target[key], dict):
                self._deep_update(target[key], value)
            else:
                target[key] = deepcopy(value)

    def _get_changes(self, old: Dict, new: Dict, path: str = "") -> Dict:
        """获取配置变更"""
        changes = {}

        all_keys = set(old.keys()) | set(new.keys())

        for key in all_keys:
            current_path = f"{path}.{key}" if path else key

            if key not in old:
                changes[current_path] = {"old": None, "new": new[key]}
            elif key not in new:
                changes[current_path] = {"old": old[key], "new": None}
            elif isinstance(old[key], dict) and isinstance(new[key], dict):
                sub_changes = self._get_changes(old[key], new[key], current_path)
                changes.update(sub_changes)
            elif old[key] != new[key]:
                changes[current_path] = {"old": old[key], "new": new[key]}

        return changes

    @staticmethod
    def _is_float(value: str) -> bool:
        """检查是否是浮点数"""
        try:
            float(value)
            return '.' in value
        except ValueError:
            return False

    async def _start_watching(self):
        """启动异步文件监控"""
        if self._watch_task and not self._watch_task.done():
            return

        self._watch_enabled = True
        self._watch_task = asyncio.create_task(self._watch_loop())

    async def _watch_loop(self):
        """异步文件监控循环"""
        if not self._config_path:
            return

        last_mtime = self._config_path.stat().st_mtime

        while self._watch_enabled:
            await asyncio.sleep(2)  # 每2秒检查一次
            try:
                if not self._config_path.exists():
                    continue

                current_mtime = self._config_path.stat().st_mtime
                if current_mtime > last_mtime:
                    self._logger.info("配置文件已变更，重新加载")
                    changes = await self.reload()
                    if changes and self._on_change_callback:
                        # 在回调中执行用户代码
                        if asyncio.iscoroutinefunction(self._on_change_callback):
                            await self._on_change_callback(changes)
                        else:
                            self._on_change_callback(changes)
                    last_mtime = current_mtime
            except Exception as e:
                self._logger.error(f"配置文件监控错误: {e}")

    def set_on_change_callback(self, callback: Callable):
        """设置配置变更回调（支持同步和异步函数）"""
        self._on_change_callback = callback

    async def stop_watching(self):
        """停止文件监控"""
        self._watch_enabled = False
        if self._watch_task and not self._watch_task.done():
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
        self._logger.info("配置文件监控已停止")

    def validate(self, schema: Dict[str, Any] = None) -> bool:
        if not schema:
            return True

        required_fields = schema.get("required", [])
        missing_fields = []

        for field in required_fields:
            if self.get(field) is None:
                missing_fields.append(field)

        if missing_fields:
            raise ConfigError(f"缺少必需的配置项: {', '.join(missing_fields)}")

        return True


# 全局实例（单例）
_default_manager = ConfigManager()


async def load_config(config_path: str = get_project_config_path(),
                      watch: bool = False) -> Dict[str, Any]:
    await _default_manager.load(config_path, watch=watch)
    return _default_manager.get_config()


def get_config(key: str = None, default: Any = None) -> Any:
    if key is None:
        return _default_manager.get_config()
    return _default_manager.get(key, default)


# 为了兼容性，保留同步版本
def load_config_sync(config_path: str = "config/default.yaml") -> Dict[str, Any]:
    """同步加载配置（不推荐在异步代码中使用）"""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
        # 如果已经在异步环境中，警告用户
        import warnings
        warnings.warn("在异步环境中使用同步加载，建议使用 await load_config()", RuntimeWarning)

        # 创建新的事件循环来执行
        new_loop = asyncio.new_event_loop()
        try:
            return new_loop.run_until_complete(load_config(config_path))
        finally:
            new_loop.close()
    except RuntimeError:
        # 没有运行中的事件循环，创建新的
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(load_config(config_path))
        finally:
            loop.close()