from collections import defaultdict

from scripts import get_project_config_path, get_project_root

count_dict = defaultdict(int)

def test_number():
    count_numbers = 1
    tim = 0

    with open(get_project_root() / "devices" / "log.txt", "r", encoding="utf-8") as f:
        for line in f:
            if 2 <= count_numbers:
                line = line.strip()
                content = line[19:].strip()
                tim = line[:19].strip()

                seen = set()
                for ch in content:
                    if ch.isdigit():
                        num = int(ch)
                        seen.add(num)

                for num in seen:
                    count_dict[num] += 1

            count_numbers += 1

    print("各数字出现的总次数:")
    for num in sorted(count_dict.keys()):
        if count_dict[num] > 0:
            print(f"数字 {num}: {count_dict[num]},  {tim}")

# def test_ip_192_168_1_117():
#     import socket
#     import threading
#
#     # 目标IP
#     target_ip = "192.168.1.117"
#
#     # 记录找到的端口数量
#     open_ports = []
#
#     def scan_port(port):
#         try:
#             s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#             s.settimeout(0.3)
#             res = s.connect_ex((target_ip, port))
#             if res == 0:
#                 print(f"✅ 端口 {port} 开放！")
#                 open_ports.append(port)  # 加入开放端口列表
#             s.close()
#         except:
#             pass
#
#     print(f"开始全端口扫描：{target_ip}")
#
#     # 开始扫描 1~65535
#     for port in range(1, 65536):
#         threading.Thread(target=scan_port, args=(port,)).start()
#
#     # 等待线程跑完（简单判断）
#     import time
#     time.sleep(5)
#
#     # ===================== 关键判断 =====================
#     if len(open_ports) == 0:
#         print("\n" + "=" * 50)
#         print("❌ 错误：未检测到任何开放端口！设备无法连接！")
#         print("=" * 50)
#     else:
#         print(f"\n扫描完成！共找到 {len(open_ports)} 个开放端口")

if __name__ == '__main__':
    test_number()
