"""
    模拟摆轮机 Modbus TCP 服务器
    纯Python手写，无任何第三方依赖
    支持：0x03读保持寄存器 / 0x06写保持寄存器
    寄存器 D0(0x0000)：1=转向 0=直行
"""
import socket
import threading
from datetime import datetime

# 配置
HOST = "0.0.0.0"
PORT = 8888
holding_registers = [0] * 10  # D0~D9 初始全0
last_value = 0


def log(message):
    """带时间戳日志"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    print(f"[{now}] {message}")


def handle_modbus_request(data):
    """处理Modbus TCP请求"""
    global holding_registers, last_value

    if len(data) < 8:
        return b''

    # 解析Modbus TCP头部
    transaction_id = data[0:2]
    protocol_id = data[2:4]
    length = data[4:6]
    unit_id = data[6]
    function_code = data[7]

    response = None

    # 功能码 03：读保持寄存器
    if function_code == 0x03:
        start_addr = (data[8] << 8) | data[9]
        count = (data[10] << 8) | data[11]
        byte_count = count * 2
        values = []
        for i in range(count):
            addr = start_addr + i
            val = holding_registers[addr] if addr < len(holding_registers) else 0
            values.append((val >> 8) & 0xFF)
            values.append(val & 0xFF)

        response = (
                transaction_id +
                protocol_id +
                (0).to_bytes(2, 'big') +
                bytes([unit_id, 0x03, byte_count]) +
                bytes(values)
        )
        response[4] = len(response) - 6

    # 功能码 06：写保持寄存器
    elif function_code == 0x06:
        addr = (data[8] << 8) | data[9]
        value = (data[10] << 8) | data[11]

        if addr < len(holding_registers):
            holding_registers[addr] = value
            # 打印D0方向变化
            if addr == 0:
                status = "转向" if value == 1 else "直行"
                log(f"📶 摆轮机 D0 = {value} → {status}")

        response = data[:12]

    return response


def handle_client(client_socket, addr):
    """处理客户端连接"""
    log(f"📡 客户端连接: {addr}")
    try:
        while True:
            data = client_socket.recv(1024)
            if not data:
                break
            response = handle_modbus_request(data)
            if response:
                client_socket.send(response)
    except:
        pass
    log(f"🔌 客户端断开: {addr}")
    client_socket.close()


def run_modbus_server():
    """启动Modbus TCP服务器"""
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((HOST, PORT))
    server_socket.listen(5)

    print("=" * 60)
    print("🟢 纯Python 摆轮机 Modbus TCP 服务器 已启动")
    print(f"📡 监听地址：{HOST}:{PORT}")
    print(f"📦 方向寄存器：D0 (0x0000)")
    print(f"🔖 规则：1=转向 | 0=直行")
    print("=" * 60)
    log("⌛ 等待客户端指令...\n")

    while True:
        try:
            client_sock, addr = server_socket.accept()
            threading.Thread(target=handle_client, args=(client_sock, addr), daemon=True).start()
        except KeyboardInterrupt:
            break

    server_socket.close()
    log("🛑 服务器已停止")


if __name__ == "__main__":
    run_modbus_server()