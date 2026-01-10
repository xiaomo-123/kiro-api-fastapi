
# 账号并发控制管理器
import logging
import redis
from typing import Optional
from ..config import settings

logger = logging.getLogger(__name__)

# Redis 连接池
redis_pool = None
redis_client = None

def init_redis():
    """初始化 Redis 连接"""
    global redis_pool, redis_client
    if redis_client is None:
        try:
            redis_pool = redis.ConnectionPool(
                host=getattr(settings, "REDIS_HOST", "localhost"),
                port=getattr(settings, "REDIS_PORT", 6379),
                db=getattr(settings, "REDIS_DB", 0),
                password=getattr(settings, "REDIS_PASSWORD", None),
                max_connections=500,
                decode_responses=True,
                socket_timeout=3,
                socket_connect_timeout=3,
                retry_on_timeout=False
            )
            redis_client = redis.Redis(connection_pool=redis_pool)
            redis_client.ping()
            logger.info("Account concurrency manager connected to Redis successfully")
        except Exception as e:
            logger.warning(f"Redis connection failed for concurrency manager: {e}")
            redis_client = None


async def acquire_account(account_id: int, max_concurrent: int = 5) -> bool:
    """尝试获取账号的使用权限

    Args:
        account_id: 账号ID
        max_concurrent: 最大并发数,默认为5

    Returns:
        bool: 是否成功获取账号使用权
    """
    if redis_client is None:
        init_redis()
        if redis_client is None:
            # Redis不可用时,允许所有请求通过
            logger.warning("Redis not available, allowing account usage without concurrency control")
            return True

    try:
        # 使用Redis的计数器来控制并发
        concurrency_key = f"account_concurrency:{account_id}"

        # 获取当前并发数
        current = redis_client.incr(concurrency_key)

        if current == 1:
            # 第一个请求,设置过期时间(10分钟)
            redis_client.expire(concurrency_key, 600)

        if current <= max_concurrent:
            logger.debug(f"Account {account_id} acquired, current concurrency: {current}/{max_concurrent}")
            return True
        else:
            # 超过并发限制,减少计数器并返回False
            redis_client.decr(concurrency_key)
            logger.debug(f"Account {account_id} concurrency limit reached: {current}/{max_concurrent}")
            return False

    except Exception as e:
        logger.error(f"Failed to acquire account {account_id}: {e}")
        # 出错时允许请求通过,避免阻塞业务
        return True


async def release_account(account_id: int, force: bool = False):
    """释放账号使用权

    Args:
        account_id: 账号ID
        force: 是否强制释放（即使计数器不存在）
    """
    if redis_client is None:
        return

    try:
        concurrency_key = f"account_concurrency:{account_id}"

        # 检查计数器是否存在
        if not redis_client.exists(concurrency_key):
            if force:
                logger.warning(f"Account {account_id} concurrency counter does not exist, but force release requested")
            else:
                logger.warning(f"Account {account_id} concurrency counter does not exist, skipping release")
            return

        current = redis_client.decr(concurrency_key)

        # 如果计数器降到0或以下,删除key
        if current <= 0:
            redis_client.delete(concurrency_key)

        logger.debug(f"Account {account_id} released, remaining concurrency: {max(current, 0)}")

        # 记录释放操作，便于调试
        logger.info(f"Account {account_id} concurrency released (force={force})")

    except Exception as e:
        logger.error(f"Failed to release account {account_id}: {e}")


async def get_account_concurrency(account_id: int) -> int:
    """获取账号当前并发数

    Args:
        account_id: 账号ID

    Returns:
        int: 当前并发数
    """
    if redis_client is None:
        return 0

    try:
        concurrency_key = f"account_concurrency:{account_id}"
        current = redis_client.get(concurrency_key)
        return int(current) if current else 0
    except Exception as e:
        logger.error(f"Failed to get account {account_id} concurrency: {e}")
        return 0
