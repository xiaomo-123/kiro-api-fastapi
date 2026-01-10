
# 数据库迁移脚本：为proxies表添加status字段
import asyncio
from sqlalchemy import text
from .database import engine

async def migrate_add_proxy_status():
    """为proxies表添加status字段"""
    async with engine.begin() as conn:
        try:
            # 检查status字段是否已存在
            result = await conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='proxies' AND column_name='status'
            """))
            exists = result.first()

            if not exists:
                # 添加status字段
                await conn.execute(text("""
                    ALTER TABLE proxies 
                    ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT '1'
                """))
                print("成功为proxies表添加status字段")
            else:
                print("proxies表已存在status字段，无需迁移")
        except Exception as e:
            print(f"迁移失败: {e}")
            raise

if __name__ == '__main__':
    asyncio.run(migrate_add_proxy_status())
