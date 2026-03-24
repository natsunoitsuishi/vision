"""
模拟调度上位机和 MES 服务器
用于接收真实系统上报的数据
"""
import asyncio
import json
from datetime import datetime
from aiohttp import web


class MockServer:
    """模拟服务器 - 同时接收调度上位机和 MES 的上报数据"""

    def __init__(self, scheduler_port=8080, mes_port=9090):
        self.scheduler_port = scheduler_port
        self.mes_port = mes_port
        self.scheduler_results = []  # 存储调度上位机收到的结果
        self.scheduler_heartbeats = []  # 存储心跳
        self.mes_records = []  # 存储 MES 记录

    async def start(self):
        """启动两个模拟服务器"""
        # 调度上位机服务器
        scheduler_app = web.Application()
        scheduler_app.router.add_post("/api/scan/result", self.handle_scheduler_result)
        scheduler_app.router.add_post("/api/heartbeat", self.handle_scheduler_heartbeat)
        scheduler_app.router.add_get("/health", self.handle_health)

        scheduler_runner = web.AppRunner(scheduler_app)
        await scheduler_runner.setup()
        scheduler_site = web.TCPSite(scheduler_runner, "0.0.0.0", self.scheduler_port)
        await scheduler_site.start()

        # MES 服务器
        mes_app = web.Application()
        mes_app.router.add_post("/api/scan/record", self.handle_mes_record)

        mes_runner = web.AppRunner(mes_app)
        await mes_runner.setup()
        mes_site = web.TCPSite(mes_runner, "0.0.0.0", self.mes_port)
        await mes_site.start()

        print(f"\n{'='*60}")
        print(f"✅ 模拟调度上位机已启动: http://0.0.0.0:{self.scheduler_port}")
        print(f"✅ 模拟 MES 服务器已启动: http://0.0.0.0:{self.mes_port}")
        print(f"{'='*60}\n")

    async def handle_scheduler_result(self, request):
        """处理调度上位机结果上报"""
        data = await request.json()
        self.scheduler_results.append(data)
        print(f"\n📤 [调度上位机] 收到扫码结果:")
        print(f"   轨迹ID: {data.get('track_id')}")
        print(f"   状态: {data.get('status')}")
        print(f"   码值: {data.get('final_code')}")
        print(f"   模式: {data.get('mode')}")
        print(f"   时间: {data.get('created_at')}")
        return web.json_response({"status": "ok", "code": 200})

    async def handle_scheduler_heartbeat(self, request):
        """处理心跳上报"""
        data = await request.json()
        self.scheduler_heartbeats.append(data)
        print(f"\n💓 [调度上位机] 收到心跳: {data.get('status')} - {data.get('timestamp')}")
        return web.json_response({"status": "ok", "code": 200})

    async def handle_mes_record(self, request):
        """处理 MES 记录上报"""
        data = await request.json()
        self.mes_records.append(data)
        print(f"\n📊 [MES] 收到扫描记录:")
        print(f"   轨迹ID: {data.get('track_id')}")
        print(f"   设备ID: {data.get('device_id')}")
        print(f"   产线ID: {data.get('line_id')}")
        print(f"   状态: {data.get('status')}")
        print(f"   码值: {data.get('final_code')}")
        print(f"   处理耗时: {data.get('process_time_ms')}ms")
        return web.json_response({"status": "ok", "code": 200})

    async def handle_health(self, request):
        """健康检查"""
        return web.json_response({"status": "healthy", "timestamp": datetime.now().isoformat()})

    def print_summary(self):
        """打印汇总信息"""
        print(f"\n{'='*60}")
        print("📊 接收数据汇总")
        print(f"{'='*60}")
        print(f"调度上位机 - 扫码结果: {len(self.scheduler_results)} 条")
        print(f"调度上位机 - 心跳: {len(self.scheduler_heartbeats)} 条")
        print(f"MES - 扫描记录: {len(self.mes_records)} 条")

        if self.scheduler_results:
            print("\n最新扫码结果:")
            last = self.scheduler_results[-1]
            print(f"  {last}")

        if self.mes_records:
            print("\n最新MES记录:")
            last = self.mes_records[-1]
            print(f"  {last}")

    def get_stats(self):
        return {
            "scheduler_results": len(self.scheduler_results),
            "scheduler_heartbeats": len(self.scheduler_heartbeats),
            "mes_records": len(self.mes_records)
        }


async def main():
    """主函数"""
    print("\n" + "🔧" * 30)
    print("模拟调度上位机和 MES 服务器")
    print("🔧" * 30)
    print("\n此服务器将接收来自视觉门系统的上报数据")
    print("请确保视觉门系统配置中的地址指向本机")
    print()

    # 创建并启动服务器
    server = MockServer(scheduler_port=8080, mes_port=9090)
    await server.start()

    print("等待接收数据... (按 Ctrl+C 停止)\n")

    try:
        # 保持运行
        while True:
            await asyncio.sleep(1)

            # 每10秒打印一次统计
            if int(datetime.now().timestamp()) % 10 == 0:
                stats = server.get_stats()
                print(f"\r📈 统计: 结果={stats['scheduler_results']}, "
                      f"心跳={stats['scheduler_heartbeats']}, "
                      f"MES={stats['mes_records']}", end="")

    except KeyboardInterrupt:
        print("\n\n正在停止...")
        server.print_summary()
        print("\n✅ 服务器已停止")


if __name__ == "__main__":
    asyncio.run(main())