import asyncio
import time

from pymodbus.client import ModbusTcpClient

PLC_IP = "192.168.1.200"
PLC_PORT = 502

Photoelectric_IP = "192.168.1.117"
Photoelectric_PORT = 501
D0_ADDR = 0
D1_ADDR = 1

T_D0 = 1.788
T_D1 = 3.9285

TIME_BIT = 500

def to_plc(addr: int, value: int):
    client_plc = ModbusTcpClient(PLC_IP, port=PLC_PORT)
    client_plc.connect()
    client_plc.write_register(addr, value)
    client_plc.close()

if __name__ == '__main__':
    str_count = "4321"

    async def handle_trigger(trigger_count: int):

        # 第1次
        if trigger_count == 1:
            await asyncio.sleep(T_D0)
            to_plc(D0_ADDR, 1)
            print("Hello 1")

        # 第2次
        elif trigger_count == 2:
            await asyncio.sleep(T_D0)
            to_plc(D0_ADDR, 2)
            print("Hello 2 (D0)")

            await asyncio.sleep(T_D1 - T_D0)
            to_plc(D1_ADDR, 2)
            print("Hello 2")

        # 第3次
        elif trigger_count == 3:
            await asyncio.sleep(T_D0)
            to_plc(D0_ADDR, 3)
            print("Hello 3 (D0)")

            await asyncio.sleep(T_D1 - T_D0)
            to_plc(D1_ADDR, 3)
            print("Hello 3")

        # 第4次
        elif trigger_count == 4:
            await asyncio.sleep(T_D0)
            to_plc(D0_ADDR, 4)
            print("Hello 4 (D0)")

            await asyncio.sleep(T_D1 - T_D0)
            to_plc(D1_ADDR, 4)
            print("Hello 4")

    async def main():
        count = 0
        client_photoelectric = ModbusTcpClient(Photoelectric_IP, port=Photoelectric_PORT)
        client_photoelectric.connect()
        print("已连接光电传感器")

        last_pe2 = False
        last_pe2_time = time.time_ns() / 1_000_000

        while True:
            result = client_photoelectric.read_discrete_inputs(address=0, count=2)
            if not result.isError():
                pe2 = result.bits[1]

                # 上升沿触发
                if not last_pe2 and pe2 and time.time_ns() / 1_000_000 - last_pe2_time > TIME_BIT:
                    count += 1
                    if count > 4:
                        count = 1

                    print(f"📌 第{count}次触发, 时间 {time.time_ns() / 1_000_000}")

                    asyncio.create_task(handle_trigger(int(str_count[count - 1])))

                last_pe2 = pe2

            await asyncio.sleep(0.05)

    if __name__ == '__main__':
        asyncio.run(main())