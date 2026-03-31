# mock_divert_server.py
"""
模拟摆轮机 TCP 服务器
用于测试，接收转向信号并打印日志
"""

import socket
import threading
import time
from datetime import datetime


class MockDivertServer:
    """模拟摆轮机 TCP 服务器"""

    def __init__(self, host: str = "0.0.0.0", port: int = 8888):
        self.host = host
        self.port = port
        self.server_socket = None
        self.running = False
        self.client_sockets = []

    def start(self):
        """启动服务器"""
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(5)
        self.running = True

        print(f"🟢 模拟摆轮机服务器已启动: {self.host}:{self.port}")
        print(f"⏰ 启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("-" * 50)

        # 启动接受连接的线程
        self.accept_thread = threading.Thread(target=self._accept_connections, daemon=True)
        self.accept_thread.start()

    def _accept_connections(self):
        """接受客户端连接"""
        while self.running:
            try:
                client_socket, client_addr = self.server_socket.accept()
                print(f"📡 新客户端连接: {client_addr}")
                self.client_sockets.append(client_socket)

                # 为每个客户端启动一个处理线程
                client_thread = threading.Thread(
                    target=self._handle_client,
                    args=(client_socket, client_addr),
                    daemon=True
                )
                client_thread.start()
            except OSError:
                break
            except Exception as e:
                print(f"❌ 接受连接错误: {e}")

    def _handle_client(self, client_socket, client_addr):
        """处理客户端消息"""
        while self.running:
            try:
                # 接收数据
                data = client_socket.recv(1024)
                if not data:
                    print(f"🔌 客户端断开连接: {client_addr}")
                    break

                # 解析消息
                message = data.decode('utf-8').strip()
                timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]

                print(f"\n{'=' * 50}")
                print(f"📨 [{timestamp}] 收到 TCP 消息:")
                print(f"   来源: {client_addr[0]}:{client_addr[1]}")
                print(f"   原始数据: {data}")
                print(f"   解码消息: {message}")

                # 解析摆轮机命令
                if message.startswith("DIVERT"):
                    parts = message.split(',')
                    if len(parts) >= 3:
                        divert_id = parts[1]
                        command = parts[2]
                        if command == "1":
                            print(f"   🎯 摆轮机 {divert_id} -> 转向 (DIVERT)")
                        else:
                            print(f"   🎯 摆轮机 {divert_id} -> 直行 (STRAIGHT)")

                print(f"{'=' * 50}\n")

                # 可选：发送响应
                response = "ACK\n"
                client_socket.send(response.encode())

            except ConnectionResetError:
                print(f"🔌 连接重置: {client_addr}")
                break
            except Exception as e:
                print(f"❌ 处理消息错误: {e}")
                break

        # 清理连接
        if client_socket in self.client_sockets:
            self.client_sockets.remove(client_socket)
        client_socket.close()

    def stop(self):
        """停止服务器"""
        print("\n🛑 正在停止模拟服务器...")
        self.running = False

        # 关闭所有客户端连接
        for client in self.client_sockets:
            try:
                client.close()
            except:
                pass
        self.client_sockets.clear()

        # 关闭服务器 socket
        if self.server_socket:
            self.server_socket.close()

        print("✅ 模拟服务器已停止")

    def send_to_clients(self, message: str):
        """向所有连接的客户端发送消息"""
        for client in self.client_sockets:
            try:
                client.send(message.encode())
            except:
                pass


def main():
    """主函数"""
    # 创建服务器
    server = MockDivertServer(host="0.0.0.0", port=8888)

    try:
        server.start()
        print("\n💡 提示:")
        print("   - 服务器正在运行，等待连接...")
        print("   - 按 Ctrl+C 停止服务器")
        print("   - 你的程序应该连接到 127.0.0.1:8888")
        print()

        # 保持服务器运行
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n")
        server.stop()


if __name__ == "__main__":
    main()