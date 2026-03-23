import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from colorama import init, Fore, Style, Back

init(autoreset=True)

class ColorHandler(logging.StreamHandler):
    """彩色日志处理器"""

    def __init__(self):
        super().__init__()
        # 设置这个处理器接受所有级别
        self.setLevel(logging.INFO)

    def emit(self, record):
        try:
            # 先检查级别
            if record.levelno < self.level:
                return

            msg = self.format(record)

            # 为不同级别设置不同颜色
            if record.levelno == logging.INFO:
                # INFO：白色（在黑底上看起来像黑色文字）
                colored_msg = f"{Fore.WHITE}{msg}{Style.RESET_ALL}"
            elif record.levelno == logging.WARNING:
                # WARNING：黄色
                colored_msg = f"{Fore.YELLOW}{msg}{Style.RESET_ALL}"
            elif record.levelno == logging.ERROR:
                # ERROR：红色
                colored_msg = f"{Fore.RED}{msg}{Style.RESET_ALL}"
            elif record.levelno == logging.CRITICAL:
                # CRITICAL：白字红底
                colored_msg = f"{Fore.WHITE}{Back.RED}{msg}{Style.RESET_ALL}"
            else:
                # DEBUG和其他：青色
                colored_msg = f"{Fore.CYAN}{msg}{Style.RESET_ALL}"

            # 写入流
            stream = self.stream
            stream.write(colored_msg + "\n")
            self.flush()

        except Exception:
            self.handleError(record)

class LoggerManager:
    """日志管理器单例"""

    _instance: Optional['LoggerManager'] = None
    _initialized: bool = False

    def __new__(cls):
        print("=== new logging manager ===")
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        print("=== init logging manager ===")
        if self._initialized:
            return

        self._initialized = True
        self._loggers: Dict[str, logging.Logger] = {}

        #   --- DEFAULT_CONFIG ---
        self._default_config = {
            'level': 'DEBUG',
            'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            'datefmt': '%H:%M:%S',
            'file_enabled': False,
            'file_path': 'logs/app.log',
            'console_enabled': True,
            'console_color': True
        }
        self._config = self._default_config.copy()

        # 初始化根日志器
        self._setup_root_logger()

    def _setup_root_logger(self):
        """设置根日志器"""
        root_logger = logging.getLogger()
        root_logger.setLevel(self._get_log_level())

        # 清除已有的处理器
        root_logger.handlers.clear()

        # 添加控制台处理器
        if self._config['console_enabled']:
            if self._config['console_color']:
                handler = ColorHandler()
            else:
                handler = logging.StreamHandler(sys.stdout)

            formatter = logging.Formatter(
                self._config['format'],
                datefmt=self._config['datefmt']
            )
            handler.setFormatter(formatter)
            root_logger.addHandler(handler)

        # 添加文件处理器
        if self._config['file_enabled']:
            try:
                log_path = Path(self._config['file_path'])
                log_path.parent.mkdir(parents=True, exist_ok=True)

                file_handler = logging.FileHandler(
                    self._config['file_path'],
                    encoding='utf-8'
                )
                file_handler.setFormatter(logging.Formatter(
                    self._config['format']
                ))
                root_logger.addHandler(file_handler)
            except Exception as e:
                print(f"无法创建文件处理器: {e}")

    def _get_log_level(self) -> int:
        """获取日志级别"""
        level_map = {
            'DEBUG': logging.DEBUG,
            'INFO': logging.INFO,
            'WARNING': logging.WARNING,
            'ERROR': logging.ERROR,
            'CRITICAL': logging.CRITICAL
        }
        return level_map.get(self._config['level'].upper(), logging.DEBUG)

    def configure(self, config: Dict[str, Any]):
        """重新配置日志系统"""
        self._config.update(config)
        self._setup_root_logger()

        # 重新配置所有已创建的日志器
        for name, logger in self._loggers.items():
            logger.setLevel(self._get_log_level())

    def get_logger(self, name: str = None) -> logging.Logger:
        """获取日志器实例"""
        if name is None:
            return logging.getLogger()

        if name not in self._loggers:
            logger = logging.getLogger(name)
            logger.setLevel(self._get_log_level())
            self._loggers[name] = logger

        return self._loggers[name]

    def set_level(self, level: str):
        """设置全局日志级别"""
        self._config['level'] = level
        log_level = self._get_log_level()

        # 设置根日志器
        logging.getLogger().setLevel(log_level)

        # 设置所有子日志器
        for logger in self._loggers.values():
            logger.setLevel(log_level)

_logger_manager = LoggerManager()

def get_logger(name: str = None) -> logging.Logger:
    return _logger_manager.get_logger(name)

def setup_logging(config: Dict[str, Any] = None) -> None:
    if config:
        _logger_manager.configure(config)

if __name__ == '__main__':
    setup_logging()
    log = get_logger(__name__)

    # 测试所有级别
    log.debug("调试信息 - 应该是青色")
    log.info("普通信息 - 应该是白色（黑色）")
    log.warning("警告信息 - 应该是黄色")
    log.error("错误信息 - 应该是红色")
    log.critical("严重错误 - 应该是白字红底")