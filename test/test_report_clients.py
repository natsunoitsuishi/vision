"""
测试上报客户端
"""
import asyncio
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from devices.report import SchedulerClient, MesClient
from config import load_config, get_config


class MockSchedulerServer:
    """模拟调度上位机服务端"""

    def __init__(self, host="127.0.0.1", port=8080):
        self.host = host
        self.port = port
        self.received_results = []
        self.received_heartbeats = []
        self._server = None

    async def start(self):
        """启动模拟服务器"""
        from aiohttp import web

        async def handle_result(request):
            data = await request.json()
            self.received_results.append(data)
            print(f"📤 调度上位机收到结果: {data}")
            return web.json_response({"status": "ok", "code": 200})

        async def handle_heartbeat(request):
            data = await request.json()
            self.received_heartbeats.append(data)
            print(f"💓 调度上位机收到心跳: {data}")
            return web.json_response({"status": "ok", "code": 200})

        async def handle_health(request):
            return web.json_response({"status": "healthy"})

        app = web.Application()
        app.router.add_post("/api/scan/result", handle_result)
        app.router.add_post("/api/heartbeat", handle_heartbeat)
        app.router.add_get("/health", handle_health)

        runner = web.AppRunner(app)
        await runner.setup()
        self._server = web.TCPSite(runner, self.host, self.port)
        await self._server.start()
        print(f"✅ 模拟调度上位机已启动: http://{self.host}:{self.port}")

    async def stop(self):
        """停止模拟服务器"""
        if self._server:
            await self._server.stop()
            print("✅ 模拟调度上位机已停止")

    def get_stats(self):
        return {
            "results": len(self.received_results),
            "heartbeats": len(self.received_heartbeats)
        }


class MockMesServer:
    """模拟 MES 服务端"""

    def __init__(self, host="127.0.0.1", port=9090):
        self.host = host
        self.port = port
        self.received_records = []
        self._server = None

    async def start(self):
        """启动模拟服务器"""
        from aiohttp import web

        async def handle_record(request):
            data = await request.json()
            self.received_records.append(data)
            print(f"📊 MES 收到记录: {data}")
            return web.json_response({"status": "ok", "code": 200})

        app = web.Application()
        app.router.add_post("/api/scan/record", handle_record)

        runner = web.AppRunner(app)
        await runner.setup()
        self._server = web.TCPSite(runner, self.host, self.port)
        await self._server.start()
        print(f"✅ 模拟 MES 服务器已启动: http://{self.host}:{self.port}")

    async def stop(self):
        """停止模拟服务器"""
        if self._server:
            await self._server.stop()
            print("✅ 模拟 MES 服务器已停止")

    def get_stats(self):
        return {"records": len(self.received_records)}


async def test_scheduler_client():
    """测试调度上位机客户端"""
    print("\n" + "=" * 60)
    print("测试调度上位机客户端")
    print("=" * 60)

    # 启动模拟服务端
    mock_server = MockSchedulerServer(host="127.0.0.1", port=8080)
    await mock_server.start()

    try:
        # 创建客户端
        client = SchedulerClient(
            host="127.0.0.1",
            port=8080,
            device_id="TEST-VG-01"
        )

        # 连接
        print("\n1. 连接调度上位机...")
        connected = await client.connect()
        print(f"   连接结果: {connected}")

        # 上报结果
        print("\n2. 上报扫码结果...")
        result = await client.report_result({
            "track_id": "T20260324120000_test001",
            "mode": "LR",
            "final_code": "QR-001",
            "status": "OK",
            "created_at": "2026-03-24T12:00:00"
        })
        print(f"   上报结果: {result}")

        # 上报心跳
        print("\n3. 上报心跳...")
        heartbeat = await client.report_heartbeat()
        print(f"   心跳上报: {heartbeat}")

        # 等待一下让服务器处理
        await asyncio.sleep(0.5)

        # 打印统计
        print("\n4. 统计信息:")
        stats = mock_server.get_stats()
        print(f"   收到结果数: {stats['results']}")
        print(f"   收到心跳数: {stats['heartbeats']}")

        # 断开连接
        print("\n5. 断开连接...")
        await client.disconnect()
        print("   已断开")

        # 测试未连接时上报
        print("\n6. 测试未连接时上报...")
        result = await client.report_result({
            "track_id": "test002",
            "final_code": "QR-002",
            "status": "OK"
        })
        print(f"   未连接时上报结果: {result}")

    finally:
        # 停止模拟服务器
        await mock_server.stop()


async def test_mes_client():
    """测试 MES 客户端"""
    print("\n" + "=" * 60)
    print("测试 MES 客户端")
    print("=" * 60)

    # 启动模拟服务端
    mock_server = MockMesServer(host="127.0.0.1", port=9090)
    await mock_server.start()

    try:
        # 创建客户端
        client = MesClient(
            host="127.0.0.1",
            port=9090,
            device_id="TEST-VG-01",
            line_id="TEST-LINE-01"
        )

        # 连接
        print("\n1. 连接 MES 系统...")
        connected = await client.connect()
        print(f"   连接结果: {connected}")

        # 上报单条记录
        print("\n2. 上报单条扫描记录...")
        result = await client.report_scan_record({
            "track_id": "T20260324120000_mes001",
            "mode": "LR",
            "final_code": "QR-001",
            "status": "OK",
            "created_at": "2026-03-24T12:00:00",
            "start_time": 1742817600.0,
            "end_time": 1742817600.242
        })
        print(f"   上报结果: {result}")

        # 上报另一条
        print("\n3. 上报另一条记录...")
        result = await client.report_scan_record({
            "track_id": "T20260324120001_mes002",
            "mode": "LR",
            "final_code": "QR-002",
            "status": "OK",
            "created_at": "2026-03-24T12:00:01"
        })
        print(f"   上报结果: {result}")

        # 等待一下
        await asyncio.sleep(0.5)

        # 打印统计
        print("\n4. 统计信息:")
        stats = mock_server.get_stats()
        print(f"   收到记录数: {stats['records']}")
        print(f"   缓存大小: {client.cache_size}")

        # 测试断线重连缓存
        print("\n5. 测试断线缓存...")
        await client.disconnect()
        result = await client.report_scan_record({
            "track_id": "T20260324120002_mes003",
            "final_code": "QR-003",
            "status": "OK"
        })
        print(f"   断线时上报结果: {result}")
        print(f"   缓存大小: {client.cache_size}")

        # 重新连接
        print("\n6. 重新连接，自动上报缓存...")
        await client.connect()
        await asyncio.sleep(0.5)
        print(f"   重新连接后缓存大小: {client.cache_size}")
        stats = mock_server.get_stats()
        print(f"   总收到记录数: {stats['records']}")

    finally:
        # 停止模拟服务器
        await mock_server.stop()


async def test_with_real_config():
    """使用真实配置测试"""
    print("\n" + "=" * 60)
    print("使用真实配置测试")
    print("=" * 60)

    # 加载配置
    await load_config()
    print("✅ 配置已加载")

    # 获取配置
    scheduler_enabled = True
    mes_enabled =  True
    device_id = "VG-01"

    print(f"\n配置信息:")
    print(f"   设备ID: {device_id}")
    print(f"   调度上位机启用: {scheduler_enabled}")
    print(f"   MES 启用: {mes_enabled}")

    if scheduler_enabled:
        scheduler_host = get_config("scheduler_client.host")
        scheduler_port = get_config("scheduler_client.port")
        print(f"   调度上位机地址: {scheduler_host}:{scheduler_port}")

        client = SchedulerClient(
            host=scheduler_host,
            port=scheduler_port,
            device_id=device_id
        )
        connected = await client.connect()
        print(f"\n连接调度上位机: {connected}")

        if connected:
            result = await client.report_result({
                "track_id": "TEST_TRACK",
                "mode": "LR",
                "final_code": "TEST_CODE",
                "status": "OK",
                "created_at": "2026-03-24T12:00:00"
            })
            print(f"上报测试结果: {result}")
            await client.disconnect()
    else:
        print("\n调度上位机未启用，跳过测试")

    if mes_enabled:
        mes_host = get_config("mes.host")
        mes_port = get_config("mes.port")
        print(f"   MES 地址: {mes_host}:{mes_port}")

        client = MesClient(
            host=mes_host,
            port=mes_port,
            device_id=device_id,
            line_id=get_config("app.line_id", "LINE-01")
        )
        connected = await client.connect()
        print(f"\n连接 MES: {connected}")

        if connected:
            result = await client.report_scan_record({
                "track_id": "TEST_TRACK_MES",
                "mode": "LR",
                "final_code": "TEST_CODE",
                "status": "OK",
                "created_at": "2026-03-24T12:00:00"
            })
            print(f"上报测试结果: {result}")
            await client.disconnect()


async def main():
    """主测试函数"""
    print("\n" + "🔧" * 30)
    print("上报客户端测试")
    print("🔧" * 30)

    print("\n请选择测试项:")
    print("1. 测试调度上位机客户端（启动模拟服务器）")
    print("2. 测试 MES 客户端（启动模拟服务器）")
    print("3. 使用真实配置测试")
    print("4. 运行所有测试")

    choice = input("\n请输入选择 (1/2/3/4): ").strip()

    if choice == "1":
        await test_scheduler_client()
    elif choice == "2":
        await test_mes_client()
    elif choice == "3":
        await test_with_real_config()
    elif choice == "4":
        await test_scheduler_client()
        await test_mes_client()
        await test_with_real_config()
    else:
        print("无效选择")


if __name__ == "__main__":
    asyncio.run(main())