import asyncio

from config.manager import ConfigManager
import config.manager

def test_config():
    import asyncio
    from config.manager import load_config, get_config

    async def main():
        # 加载配置
        await load_config()

        # 获取配置
        db_path = get_config("photoelectric.host")

        print(db_path)

    asyncio.run(main())


if __name__ == '__main__':
    test_config()
    # test_config()