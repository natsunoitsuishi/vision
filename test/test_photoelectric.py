from random import choice

from devices.photoelectric import PhotoelectricClient


def test_photoelectric():
    # test_photoelectric_client.py
    """
    测试 Modbus TCP 客户端（光电传感器模拟器）
    """
    import asyncio
    import sys
    import time
    from pathlib import Path

    # # 添加项目根目录到路径
    # sys.path.insert(0, str(Path(__file__).parent))

    from services.event_bus import EventBus
    from domain.enums import EventType
    from config.manager import load_config, get_config

    class TestEventHandler:
        """测试事件处理器"""

        def __init__(self):
            self.pe1_events = []
            self.pe2_events = []

        def on_event(self, event):
            """处理事件"""
            if event.event_type == EventType.PE_RISE:
                channel = event.payload.get("channel")
                if channel == 1:
                    self.pe1_events.append(("RISE", event.payload.get("timestamp")))
                    print(f"✅ 收到 PE1 上升沿事件")
                elif channel == 2:
                    self.pe2_events.append(("RISE", event.payload.get("timestamp")))
                    print(f"✅ 收到 PE2 上升沿事件")

            elif event.event_type == EventType.PE_FALL:
                channel = event.payload.get("channel")
                if channel == 1:
                    self.pe1_events.append(("FALL", event.payload.get("timestamp")))
                    print(f"✅ 收到 PE1 下降沿事件")
                elif channel == 2:
                    self.pe2_events.append(("FALL", event.payload.get("timestamp")))
                    print(f"✅ 收到 PE2 下降沿事件")

    async def test_connection():
        """测试1: 连接测试"""
        print("\n" + "=" * 60)
        print("测试1: 连接测试")
        print("=" * 60)

        # 加载配置
        await load_config("config/default.yaml")

        # 获取 Modbus 配置
        modbus_config = get_config("modbus", {
                 "host": "127.0.0.1",
                 "port": 15020,
                 "timeout": 3.0
        })
        # if not modbus_config:
        #     print("⚠️  配置中没有 modbus 配置，使用默认配置")
        #     modbus_config = {
        #         "host": "127.0.0.1",
        #         "port": 15020,
        #         "timeout": 3.0
        #     }

        # 创建事件总线
        event_bus = EventBus()

        # 创建客户端
        client = PhotoelectricClient(event_bus)

        try:
            print(f"正在连接 {modbus_config['host']}:{modbus_config['port']}...")
            await client.connect()
            print("✅ 连接成功")

            # 读取初始状态
            di1, di2 = await client.read_discrete_inputs()
            print(f"初始状态: DI1={di1}, DI2={di2}")

            return client, event_bus

        except Exception as e:
            print(f"❌ 连接失败: {e}")
            return None, None

    async def test_monitoring(client: PhotoelectricClient, event_bus: EventBus, duration: int = 10):
        """测试2: 监控测试"""
        print("\n" + "=" * 60)
        print(f"测试2: 监控测试 (运行 {duration} 秒)")
        print("=" * 60)
        print("请手动触发光电传感器（遮挡/松开）")
        print(f"将监控 {duration} 秒...")

        # 创建事件处理器
        handler = TestEventHandler()

        # 订阅事件
        def on_event(event):
            handler.on_event(event)

        event_bus.subscribe(EventType.PE_RISE, on_event)
        event_bus.subscribe(EventType.PE_FALL, on_event)

        # 启动监控
        await client.start_monitoring(interval_ms=20)  # 20ms 轮询
        print("✅ 监控已启动")

        # 运行指定时间
        try:
            for i in range(duration):
                print(f"  监控中... {i + 1}/{duration} 秒", end="\r")
                await asyncio.sleep(1)
            print("\n")
        except KeyboardInterrupt:
            print("\n⏹️  用户中断")

        # 停止监控
        await client.stop_monitoring()
        print("✅ 监控已停止")

        # 打印统计
        print(f"\n统计:")
        print(f"  PE1 事件: {len(handler.pe1_events)} 次")
        for event in handler.pe1_events:
            print(f"    - {event[0]}")
        print(f"  PE2 事件: {len(handler.pe2_events)} 次")
        for event in handler.pe2_events:
            print(f"    - {event[0]}")

        return handler

    async def test_manual_trigger(client: PhotoelectricClient, event_bus: EventBus):
        """测试3: 手动触发测试"""
        print("\n" + "=" * 60)
        print("测试3: 手动触发测试")
        print("=" * 60)
        print("请手动触发光电传感器（每次触发会显示事件）")
        print("输入 'q' 退出测试\n")

        # 创建事件处理器
        handler = TestEventHandler()

        # 订阅事件
        def on_event(event):
            handler.on_event(event)
            ts = event.payload.get("timestamp", 0)
            state = event.payload.get("state")
            channel = event.payload.get("channel")

            if event.event_type == EventType.PE_RISE:
                print(f"  🟢 光电{channel} 被触发 (遮挡) at {ts:.3f}")
            else:
                print(f"  🔴 光电{channel} 恢复 (松开) at {ts:.3f}")

        event_bus.subscribe(EventType.PE_RISE, on_event)
        event_bus.subscribe(EventType.PE_FALL, on_event)

        # 启动监控
        await client.start_monitoring(interval_ms=20)
        print("✅ 监控已启动，请触发光电传感器...\n")

        # 等待用户输入
        try:
            while True:
                cmd = await asyncio.get_event_loop().run_in_executor(None, input, "输入 'q' 退出: ")
                if cmd.lower() == 'q':
                    break
        except KeyboardInterrupt:
            pass

        # 停止监控
        await client.stop_monitoring()
        print("\n✅ 测试完成")

    async def test_read_write(client: PhotoelectricClient):
        """测试4: 读写测试"""
        print("\n" + "=" * 60)
        print("测试4: 连续读取测试")
        print("=" * 60)

        print("连续读取 DI 状态 10 次...")
        for i in range(10):
            try:
                di1, di2 = await client.read_discrete_inputs()
                print(f"  [{i + 1:2d}] DI1={int(di1)}, DI2={int(di2)}")
                await asyncio.sleep(0.5)
            except Exception as e:
                print(f"  ❌ 读取失败: {e}")

        print("✅ 读取测试完成")

    async def test_health_check(client: PhotoelectricClient):
        """测试5: 健康检查"""
        print("\n" + "=" * 60)
        print("测试5: 健康检查")
        print("=" * 60)

        health = client.get_health()
        print(f"  设备ID: {health.device_id}")
        print(f"  设备类型: {health.device_type}")
        print(f"  状态: {health.status.value}")
        print(f"  消息: {health.message}")
        print(f"  最后心跳: {health.last_heartbeat_ts}")

        print("✅ 健康检查完成")

    async def main():
        """主测试函数"""
        print("=" * 60)
        print("光电传感器客户端测试")
        print("=" * 60)
        print("\n注意事项:")
        print("1. 确保设备模拟程序正在运行")
        print("2. 默认地址: 127.0.0.1:15020")
        print("3. 确保配置正确")
        print()

        # 测试1: 连接测试
        client, event_bus = await test_connection()
        if not client:
            print("\n❌ 连接失败，测试终止")
            print("请检查:")
            print("  1. 设备模拟程序是否运行")
            print("  2. 配置中的 host 和 port 是否正确")
            print("  3. 防火墙是否允许连接")
            return

        try:
            # 测试4: 连续读取
            await test_read_write(client)

            # 测试5: 健康检查
            await test_health_check(client)

            # 选择测试模式
            print("\n" + "=" * 60)
            print("选择测试模式:")
            print("  1. 自动监控测试 (运行10秒)")
            print("  2. 手动触发测试 (手动触发光电)")
            print("  3. 只测试连接和读取")
            print("=" * 60)

            # choice = await asyncio.get_event_loop().run_in_executor(None, input, "请选择 (1/2/3): ")
            choice = "2"

            if choice == "1":
                await test_monitoring(client, event_bus, duration=10)
            elif choice == "2":
                await test_manual_trigger(client, event_bus)
            else:
                print("仅测试连接和读取")

        except KeyboardInterrupt:
            print("\n⏹️  测试被中断")
        except Exception as e:
            print(f"\n❌ 测试异常: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # 断开连接
            await client.disconnect()
            print("\n✅ 已断开连接")
            print("测试完成")


    asyncio.run(main())


if __name__ == '__main__':
    test_photoelectric()