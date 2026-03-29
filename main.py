#!/usr/bin/env python3
"""
扫码视觉门上位机 - 程序入口
"""
import asyncio
import sys
from pathlib import Path

from app.lifecycle import AppController

# 添加项目根目录到 Python 路径
# sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtWidgets import QApplication
from qasync import QEventLoop
from infra import get_logger

def main() -> None:
    """
    程序主入口

    启动流程：
    1. 创建 Qt 应用
    2. 创建融合事件循环 (qasync)
    3. 创建应用控制器
    4. 启动应用
    """
    # 1. 创建 Qt 应用（必须最先创建）
    qt_app = QApplication(sys.argv)

    # 2. 创建融合事件循环（让 Qt 和 asyncio 一起工作）
    loop = QEventLoop(qt_app)
    asyncio.set_event_loop(loop)

    # 3. 创建应用控制器
    controller = AppController(qt_app, loop)

    # 4. 启动应用
    try:
        with loop:
            # 启动异步初始化
            loop.run_until_complete(controller.startup())

            # 进入主循环（程序一直运行直到关闭）
            loop.run_forever()

    except KeyboardInterrupt:
        print("\n收到中断信号，正在关闭...")
    except Exception as e:
        print(f"程序异常退出: {e}")
        get_logger(__name__).exception("程序异常退出")
    finally:
        # 确保资源被释放
        try:
            # 在现有循环中运行关闭
            if not loop.is_closed():
                loop.run_until_complete(controller.shutdown())
        except Exception:
            pass
        finally:
            # 关闭 Qt 应用
            qt_app.quit()
            # 关闭事件循环
            loop.close()


if __name__ == "__main__":
    main()