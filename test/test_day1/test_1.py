# def day1():
#
#     import socket
#     import time
#
#     # 相机配置（与截图一致）
#     CAMERA_IP = "192.168.10.79"
#     CAMERA_PORT = 1025
#     TRIGGER_CMD = b"start"  # 触发拍照指令
#     STOP_CMD = b"stop"  # 停止触发指令
#
#
#     def tcp_trigger_capture_on():
#         try:
#             # 创建TCP客户端套接字
#             with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
#                 s.connect((CAMERA_IP, CAMERA_PORT))
#                 print(f"已连接到相机: {CAMERA_IP}:{CAMERA_PORT}")
#                 # 发送触发指令拍照
#                 s.sendall(TRIGGER_CMD)
#                 print("已发送触发指令: start")
#
#                 # 可选：等待拍照完成，再发送停止指令
#                 # time.sleep(1)
#                 # s.sendall(STOP_CMD)
#                 # print("已发送停止指令: stop")
#
#         except Exception as e:
#             print(f"触发失败: {e}")
#
#     def tcp_trigger_capture_off():
#         try:
#             # 创建TCP客户端套接字
#             with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
#                 s.connect((CAMERA_IP, CAMERA_PORT))
#                 print(f"已连接到相机: {CAMERA_IP}:{CAMERA_PORT}")
#
#                 # 发送触发指令拍照
#                 s.sendall(STOP_CMD)
#                 print("已发送触发指令: stop")
#
#                 # 可选：等待拍照完成，再发送停止指令
#                 # time.sleep(1)
#                 # s.sendall(STOP_CMD)
#                 # print("已发送停止指令: stop")
#
#         except Exception as e:
#             print(f"触发失败: {e}")
#
#     tcp_trigger_capture_on()
#
#     import socket
#
#     # 目标 IP 和端口
#     ip = "192.168.10.79"
#     port = 1024
#     while True:
#         # 创建 TCP 连接
#         s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#         s.settimeout(5)
#
#         try:
#             # 连接
#             s.connect((ip, port))
#             print("连接成功！")
#
#             # 发送数据（HTTP 协议最小请求）
#             s.send(b"GET / HTTP/1.1\r\nHost: test\r\n\r\n")
#
#             # 接收回复
#             reply = s.recv(4096)
#             print("服务器返回:\n", reply.decode())
#
#         except Exception as e:
#             print("失败:", e)
#         finally:
#             s.close()
#
# if __name__ == "__main__":
#     day1()


def test_day1():
# 最简单用法 - TCP连接
    import socket


    def read_di(ip="192.168.1.117", port=500, slave=1):
        """读取开关量输入"""
        sock = socket.socket()
        sock.connect((ip, port))

        # 读取前8路DI
        req = bytes([slave, 0x02, 0x00, 0x00, 0x00, 0x08])
        sock.send(req)

        resp = sock.recv(1024)
        sock.close()

        if len(resp) >= 5:
            data = resp[3]
            result = []
            for i in range(8):
                result.append(bool((data >> i) & 1))
            return result
        return []


    # 使用
    di = read_di()
    print(f"DI1-DI8: {di}")