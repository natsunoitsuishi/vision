# helloday.py 脚本说明文档

## 1. 脚本概述

`helloday.py` 是一个用于监控光电传感器状态并与 PLC 进行交互的 Python 脚本。该脚本通过 Modbus TCP 协议与光电传感器和 PLC 通信，实现了基于光电传感器触发的自动化控制逻辑。

## 2. 核心功能

- **光电传感器监控**：实时读取光电传感器的状态
- **上升沿触发检测**：检测 PE2 传感器的上升沿信号
- **PLC 控制**：根据触发次数向 PLC 的不同寄存器写入不同值
- **时序控制**：通过延时参数控制不同操作的执行时间

## 3. 关键参数

| 参数 | 当前值 | 说明 | 建议值 |
|------|--------|------|--------|
| T_D0 | 1.788 | D0 操作的延时时间（秒） | **≥ 2.5 秒** |
| T_D1 | 3.9285 | D1 操作的延时时间（秒） | **≥ 5.0 秒** |
| TIME_BIT | 500 | 触发间隔阈值（毫秒） | 保持不变 |

## 4. 工作流程

1. **初始化连接**：连接到光电传感器
2. **状态监控**：循环读取光电传感器的状态
3. **触发检测**：检测 PE2 传感器的上升沿信号
4. **时序控制**：根据触发次数和延时参数执行相应操作
5. **PLC 通信**：向 PLC 的 D0 和 D1 寄存器写入值

## 5. 代码结构

### 5.1 配置参数

```python
PLC_IP = "192.168.1.200"
PLC_PORT = 502

Photoelectric_IP = "192.168.1.117"
Photoelectric_PORT = 501
D0_ADDR = 0
D1_ADDR = 1

T_D0 = 1.788  # D0 操作延时
T_D1 = 3.9285  # D1 操作延时

TIME_BIT = 500  # 触发间隔阈值（毫秒）
```

### 5.2 核心函数

#### 5.2.1 PLC 写入函数

```python
def to_plc(addr: int, value: int):
    client_plc = ModbusTcpClient(PLC_IP, port=PLC_PORT)
    client_plc.connect()
    client_plc.write_register(addr, value)
    client_plc.close()
```

#### 5.2.2 触发处理函数

```python
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
```

#### 5.2.3 主函数

```python
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
```

## 6. 延时参数分析

### 6.1 当前延时设置的问题

- **T_D0 = 1.788 秒**：延时过短，可能导致：
  - 光电传感器信号不稳定时，PLC 操作过早执行
  - 机械动作未完成，影响控制精度
  - 可能与其他系统操作冲突

- **T_D1 = 3.9285 秒**：延时过短，可能导致：
  - D1 操作与 D0 操作间隔不足
  - 系统响应时间不足，影响整体控制效果
  - 在复杂场景下可能导致操作失败

### 6.2 建议的延时设置

- **T_D0 ≥ 2.5 秒**：
  - 提供足够的时间让系统稳定
  - 确保光电传感器信号可靠
  - 为机械动作提供充足的执行时间

- **T_D1 ≥ 5.0 秒**：
  - 确保 D0 和 D1 操作之间有足够的间隔
  - 为系统提供充分的响应时间
  - 适应复杂场景下的控制需求

## 7. 触发逻辑

1. **第1次触发**：
   - 等待 T_D0 秒
   - 向 D0 寄存器写入 1

2. **第2次触发**：
   - 等待 T_D0 秒
   - 向 D0 寄存器写入 2
   - 等待 T_D1 - T_D0 秒
   - 向 D1 寄存器写入 2

3. **第3次触发**：
   - 等待 T_D0 秒
   - 向 D0 寄存器写入 3
   - 等待 T_D1 - T_D0 秒
   - 向 D1 寄存器写入 3

4. **第4次触发**：
   - 等待 T_D0 秒
   - 向 D0 寄存器写入 4
   - 等待 T_D1 - T_D0 秒
   - 向 D1 寄存器写入 4

5. **循环**：触发次数超过 4 后重置为 1

## 8. 系统架构

```
+-------------------+    +-------------------+    +-------------------+
| 光电传感器 (PE2)   | -> |  helloday.py 脚本   | -> |     PLC 控制器     |
+-------------------+    +-------------------+    +-------------------+
                           |
                           | 1. 监控 PE2 状态
                           | 2. 检测上升沿
                           | 3. 执行延时
                           | 4. 写入 PLC 寄存器
```

## 9. 运行环境

- **Python 3.7+**
- **pymodbus 库**：用于 Modbus TCP 通信
- **异步编程**：使用 asyncio 实现非阻塞操作

## 10. 注意事项

1. **网络连接**：确保 PLC 和光电传感器的 IP 地址正确且网络连接正常
2. **权限设置**：确保脚本有足够的权限访问网络资源
3. **延时调整**：根据实际系统响应时间调整 T_D0 和 T_D1 的值
4. **错误处理**：当前代码缺少完整的错误处理机制，建议在生产环境中添加
5. **日志记录**：建议添加详细的日志记录，便于故障排查

## 11. 总结

`helloday.py` 脚本是一个简单但实用的自动化控制工具，通过监控光电传感器状态并与 PLC 交互，实现了基于时序的自动化控制。通过合理调整 T_D0 和 T_D1 延时参数，可以提高系统的稳定性和可靠性，适应不同场景下的控制需求。

**关键建议**：将 T_D0 调整为 ≥ 2.5 秒，T_D1 调整为 ≥ 5.0 秒，以确保系统有足够的响应时间和稳定性。