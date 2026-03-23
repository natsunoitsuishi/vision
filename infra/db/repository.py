import asyncio
import sqlite3
from contextlib import contextmanager
from time import sleep
from typing import Tuple

import config.manager
from config.manager import ConfigManager, get_config
from scripts.util import get_project_root


class SQLiteRepository:
    def __init__(self):
        """
        同步构造函数：只做初始化，不做异步/阻塞操作
        """
        self.db_path = get_project_root() / "data" / "db" / "vision.db"

    @contextmanager
    def _get_connection(self):
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            if conn:
                conn.close()

    # ==========================
    # 内部同步检查（不对外）
    # ==========================
    def _check_connection_sync(self) -> Tuple[bool, str]:
        try:
            with self._get_connection() as conn:
                conn.execute("SELECT 1")  # 真正测试连接
                print(get_config("database"))
                return True, "✅ SQLite 连接成功"

        except sqlite3.OperationalError as e:
            return False, f"❌ 数据库被锁定或无法打开: {e}"
        except sqlite3.DatabaseError as e:
            return False, f"❌ 数据库文件损坏: {e}"
        except Exception as e:
            return False, f"❌ 数据库连接失败: {str(e)}"

    # ==========================
    # 异步检查连接（给你用在 AppController）
    # ==========================

    async def initialize_database(self) -> Tuple[bool, str]:
        # 放到线程里跑，不阻塞 asyncio
        return await asyncio.to_thread(self._check_connection_sync)

    async def save_camera_result(self, param):
        pass

    async def save_scan_record(self, param):
        pass

    async def save_alarm(self, alarm):
        pass


if __name__ == '__main__':
    pass