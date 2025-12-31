"""
数据库序列同步脚本
用于同步PostgreSQL的自增序列，确保序列值大于表中最大的ID值
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import text
from ..config import settings
import logging

logger = logging.getLogger(__name__)


async def sync_account_sequence():
    """同步accounts表的序列"""
    try:
        engine = create_async_engine(settings.DATABASE_URL)
        async with engine.begin() as conn:
            # 获取当前序列值
            result = await conn.execute(text("SELECT last_value FROM accounts_id_seq"))
            last_value = result.scalar()

            # 获取表中最大的ID
            result = await conn.execute(text("SELECT MAX(id) FROM accounts"))
            max_id = result.scalar()

            logger.info(f"当前序列值: {last_value}, 表中最大ID: {max_id}")

            # 如果序列值小于或等于最大ID，则更新序列
            if max_id is not None and last_value <= max_id:
                new_value = max_id + 1
                await conn.execute(text(f"SELECT setval('accounts_id_seq', {new_value}, true)"))
                logger.info(f"序列已更新为: {new_value}")
            else:
                logger.info("序列值正常，无需更新")

        await engine.dispose()
        return True
    except Exception as e:
        logger.error(f"同步序列失败: {e}")
        return False


async def sync_all_sequences():
    """同步所有表的序列"""
    try:
        engine = create_async_engine(settings.DATABASE_URL)
        async with engine.begin() as conn:
            # 同步accounts表
            await conn.execute(text("""
                SELECT setval('accounts_id_seq', 
                    COALESCE((SELECT MAX(id) FROM accounts), 0) + 1, 
                    true)
            """))

            # 同步users表
            await conn.execute(text("""
                SELECT setval('users_id_seq', 
                    COALESCE((SELECT MAX(id) FROM users), 0) + 1, 
                    true)
            """))

            # 同步api_keys表
            await conn.execute(text("""
                SELECT setval('api_keys_id_seq', 
                    COALESCE((SELECT MAX(id) FROM api_keys), 0) + 1, 
                    true)
            """))

            # 同步proxies表
            await conn.execute(text("""
                SELECT setval('proxies_id_seq', 
                    COALESCE((SELECT MAX(id) FROM proxies), 0) + 1, 
                    true)
            """))

        await engine.dispose()
        logger.info("所有表序列同步完成")
        return True
    except Exception as e:
        logger.error(f"同步所有序列失败: {e}")
        return False
