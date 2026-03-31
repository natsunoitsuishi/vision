import sys


def test_qasync():
    import asyncio
    from PySide2.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget
    from qasync import QEventLoop

    async def background_task(button):
        count = 0
        while True:
            await asyncio.sleep(1)
            count += 1
            button.setText(f"点了 {count} 次? 我还在后台计数: {count}")

    app = QApplication([])

    windows = QWidget()
    windows.setWindowTitle("qasync example")
    layout = QVBoxLayout()

    button = QPushButton("click me")
    layout.addWidget(button)
    windows.setLayout(layout)
    windows.show()

    loop = QEventLoop()
    asyncio.set_event_loop(loop)

    loop.create_task(background_task(button))

    def on_click():
        current_text = button.text()
        # 在点击时，后台任务仍然在运行，UI 不会卡
        button.setText("你点了我！后台还在计数呢")

    button.clicked.connect(on_click)

    # 运行程序
    with loop:
        loop.run_forever()

if __name__ == '__main__':
    test_qasync()