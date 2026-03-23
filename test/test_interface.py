# tests/test_integration.py
import asyncio
import sys
from pathlib import Path

from devices.camera import OptCameraClient
from devices.photoelectric import PhotoelectricClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.event_bus import create_event_bus

from domain.enums import EventType
from domain.models import AppEvent


class TestRuntimeHandler:
    """模拟 RuntimeService 的测试处理器"""

    def __init__(self, bus):
        self.bus = bus
        self.pe1_count = 0
        self.pe2_count = 0
        self.camera_results = []

        # 注册事件处理器
        self._register_handlers()

    def _register_handlers(self):
        self.bus.subscribe(EventType.PE_RISE, self.on_pe_rise)
        self.bus.subscribe(EventType.PE_FALL, self.on_pe_fall)
        self.bus.subscribe(EventType.CAMERA_RESULT, self.on_camera_result)

    async def on_pe_rise(self, event: AppEvent):
        if event.payload.get("channel") == 1:
            self.pe1_count += 1
            print(f"  📍 PE1上升: 第{self.pe1_count}次")
        elif event.payload.get("channel") == 2:
            self.pe2_count += 1
            print(f"  📍 PE2上升: 第{self.pe2_count}次")

    async def on_pe_fall(self, event: AppEvent):
        print(f"  📍 PE下降: channel={event.payload.get('channel')}")

    async def on_camera_result(self, event: AppEvent):
        self.camera_results.append(event.payload)
        print(f"  📸 相机结果: camera={event.payload.get('camera_id')}, "
              f"result={event.payload.get('result')}, code={event.payload.get('code')}")


async def test_modbus_connection():
    """测试Modbus连接和DI监控"""
    print("\n" + "=" * 50)
    print("测试Modbus连接")

    # 创建事件总线
    bus = create_event_bus("TestBus")

    # 创建处理器
    handler = TestRuntimeHandler(bus)

    # 创建Modbus客户端
    modbus_config = {
        "host": "127.0.0.1",
        "port": 15020,
        "timeout": 3.0
    }
    modbus = PhotoelectricClient(modbus_config, bus)

    try:
        print("1. 连接Modbus...")
        await modbus.connect()
        print("   ✅ Modbus连接成功")

        print("2. 启动DI监控...")
        await modbus.start_monitoring(interval_ms=50)
        print("   ✅ DI监控已启动")

        print("\n3. 等待DI事件（需要在设备模拟程序中触发）...")
        print("   提示：请在设备模拟程序GUI中点击'开始场景'")

        # 等待10秒
        for i in range(10):
            await asyncio.sleep(1)
            print(f"   {10 - i}秒...")
            # if handler.pe1_count > 0:
            #     break

        print(f"\n4. 统计结果:")
        print(f"   PE1事件: {handler.pe1_count}次")
        print(f"   PE2事件: {handler.pe2_count}次")

    except Exception as e:
        print(f"❌ 错误: {e}")
        print("   提示：请确保设备模拟程序已启动并运行")

    finally:
        print("\n5. 清理...")
        await modbus.stop_monitoring()
        await modbus.disconnect()
        await bus.stop()
        print("   ✅ 清理完成")


async def test_camera_connection():
    """测试相机连接和结果接收"""
    print("\n" + "=" * 50)
    print("测试相机连接")

    bus = create_event_bus("TestBus")
    handler = TestRuntimeHandler(bus)

    # 创建相机客户端
    camera1_config = {"host": "127.0.0.1", "port": 16001}
    camera1 = OptCameraClient(1, camera1_config, bus)

    try:
        print("1. 连接相机1...")
        await camera1.connect()
        print("   ✅ 相机1连接成功")

        print("2. 启动扫码会话...")
        await camera1.start_scan_session()
        print("   ✅ 扫码会话已启动")

        print("\n3. 等待相机结果（需要在设备模拟程序中触发）...")
        print("   提示：请在设备模拟程序GUI中点击'开始场景'")

        # 等待15秒
        for i in range(15):
            await asyncio.sleep(1)
            print(f"   {15 - i}秒...")
            # if len(handler.camera_results) > 0:
            #     break

        print(f"\n4. 统计结果:")
        print(f"   收到相机结果: {len(handler.camera_results)}条")
        for result in handler.camera_results:
            print(f"   - camera={result['camera_id']}, "
                  f"result={result['result']}, code={result['code']}")

    except Exception as e:
        print(f"❌ 错误: {e}")
        print("   提示：请确保设备模拟程序已启动并运行")

    finally:
        print("\n5. 清理...")
        await camera1.stop_scan_session()
        await camera1.disconnect()
        await bus.stop()
        print("   ✅ 清理完成")


async def test_full_flow():
    """测试完整流程（Modbus + 相机）"""
    print("\n" + "=" * 50)
    print("测试完整流程")
    print("⚠️ 警告：此测试需要设备模拟程序已启动并运行")
    print("=" * 50)

    bus = create_event_bus("TestBus", max_queue_size=100)
    handler = TestRuntimeHandler(bus)

    # 创建Modbus客户端
    modbus_config = {"host": "127.0.0.1", "port": 15020}
    modbus = PhotoelectricClient(modbus_config, bus)

    # 创建相机客户端
    camera1 = OptCameraClient(1, {"host": "127.0.0.1", "port": 16001}, bus)
    camera2 = OptCameraClient(2, {"host": "127.0.0.1", "port": 16002}, bus)

    try:
        # 连接所有设备
        print("\n1. 连接设备...")
        await modbus.connect()
        await camera1.connect()
        await camera2.connect()
        print("   ✅ 所有设备连接成功")

        # 启动服务
        print("\n2. 启动服务...")
        await modbus.start_monitoring(interval_ms=20)
        await camera1.start_scan_session()
        await camera2.start_scan_session()
        print("   ✅ 所有服务已启动")

        print("\n3. 等待事件（30秒）...")
        print("   请在设备模拟程序GUI中点击'开始场景'")

        # 运行30秒
        for i in range(30):
            await asyncio.sleep(1)
            if i % 5 == 0:
                print(f"   {30 - i}秒...")
                stats = bus.get_stats()
                print(f"   总线统计: 发布={stats['published']}, 处理={stats['processed']}")

        # 打印统计
        print("\n4. 最终统计:")
        print(f"   PE1事件: {handler.pe1_count}")
        print(f"   PE2事件: {handler.pe2_count}")
        print(f"   相机结果: {len(handler.camera_results)}")
        print(f"   事件总线: 发布={bus.get_stats()['published']}, "
              f"处理={bus.get_stats()['processed']}")

    except Exception as e:
        print(f"❌ 错误: {e}")

    finally:
        print("\n5. 清理...")
        await modbus.stop_monitoring()
        await camera1.stop_scan_session()
        await camera2.stop_scan_session()
        await modbus.disconnect()
        await camera1.disconnect()
        await camera2.disconnect()
        await bus.stop()
        print("   ✅ 清理完成")


async def main():
    """主测试入口"""
    print("\n" + "🔧" * 20)
    print("设备模拟程序集成测试")
    print("🔧" * 20)

    print("\n请选择测试项:")
    print("1. 测试Modbus连接")
    print("2. 测试相机连接")
    print("3. 测试完整流程")

    choice = input("\n请输入选择 (1/2/3): ").strip()

    if choice == "1":
        await test_modbus_connection()
    elif choice == "2":
        await test_camera_connection()
    elif choice == "3":
        await test_full_flow()
    else:
        print("无效选择")


if __name__ == "__main__":
    asyncio.run(main())