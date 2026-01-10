# API Key 管理服务
import logging
import redis
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..db.database import AsyncSessionLocal
from ..db.models import ApiKey
from ..config import settings

logger = logging.getLogger(__name__)

# Redis 连接
redis_client: Optional[redis.Redis] = None

# API Key Redis 前缀
API_KEY_PREFIX = "apikey:"
API_KEY_SET = "apikeys:active"


def init_redis():
    """初始化 Redis 连接"""
    global redis_client
    if redis_client is None:
        try:
            redis_client = redis.Redis(
                host=getattr(settings, "REDIS_HOST", "localhost"),
                port=getattr(settings, "REDIS_PORT", 6379),
                db=getattr(settings, "REDIS_DB", 0),
                password=getattr(settings, "REDIS_PASSWORD", None),
                decode_responses=True,
                socket_timeout=3,
                socket_connect_timeout=3,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            redis_client.ping()
            logger.info("API Key Manager: Redis connected successfully")
        except Exception as e:
            logger.error(f"API Key Manager: Failed to connect to Redis: {e}")
            redis_client = None
    return redis_client


def close_redis():
    """关闭 Redis 连接"""
    global redis_client
    if redis_client is not None:
        try:
            redis_client.close()
            logger.info("API Key Manager: Redis connection closed")
        except Exception as e:
            logger.error(f"API Key Manager: Error closing Redis: {e}")
        finally:
            redis_client = None


async def load_apikeys_to_redis():
    """从数据库加载所有状态为1的API Key到Redis"""
    global redis_client
    if redis_client is None:
        logger.warning("API Key Manager: Redis not available, skipping API Key load")
        return

    try:
        # 从数据库获取所有API Key
        async with AsyncSessionLocal() as db:
            stmt = select(ApiKey).filter(ApiKey.status == "1")
            result = await db.execute(stmt)
            apikeys = result.scalars().all()

            # 清空Redis中的API Key
            redis_client.delete(API_KEY_SET)

            # 删除所有API Key相关的键
            existing_keys = redis_client.keys(f"{API_KEY_PREFIX}*")
            if existing_keys:
                redis_client.delete(*existing_keys)

            # 将API Key添加到Redis
            pipe = redis_client.pipeline()
            for apikey in apikeys:
                key = f"{API_KEY_PREFIX}{apikey.api_key}"
                # 存储API Key详细信息
                pipe.hset(key, "id", str(apikey.id))
                pipe.hset(key, "api_key", apikey.api_key)
                pipe.hset(key, "description", apikey.description or "")
                pipe.hset(key, "status", apikey.status)
                # 添加到活跃API Key集合
                pipe.sadd(API_KEY_SET, apikey.api_key)

            pipe.execute()
            logger.info(f"API Key Manager: Loaded {len(apikeys)} active API Keys to Redis")

    except Exception as e:
        logger.error(f"API Key Manager: Failed to load API Keys to Redis: {e}", exc_info=True)


async def verify_api_key(api_key: str) -> Optional[dict]:
    """
    从Redis验证API Key

    Args:
        api_key: 要验证的API Key

    Returns:
        如果验证成功，返回API Key信息字典；否则返回None
    """
    global redis_client
    if redis_client is None:
        logger.warning("API Key Manager: Redis not available, cannot verify API Key")
        return None

    try:
        # 检查API Key是否在活跃集合中
        if not redis_client.sismember(API_KEY_SET, api_key):
            logger.debug(f"API Key Manager: API Key not found in active set: {api_key}")
            return None

        # 获取API Key详细信息
        key = f"{API_KEY_PREFIX}{api_key}"
        apikey_info = redis_client.hgetall(key)

        if not apikey_info:
            logger.warning(f"API Key Manager: API Key info not found in Redis: {api_key}")
            return None

        # 检查状态
        if apikey_info.get("status") != "1":
            logger.warning(f"API Key Manager: API Key is disabled: {api_key}")
            return None

        return apikey_info

    except Exception as e:
        logger.error(f"API Key Manager: Error verifying API Key: {e}", exc_info=True)
        return None


async def add_apikey_to_redis(apikey: ApiKey):
    """
    添加API Key到Redis

    Args:
        apikey: API Key对象
    """
    global redis_client
    if redis_client is None:
        logger.warning("API Key Manager: Redis not available, cannot add API Key")
        return

    try:
        key = f"{API_KEY_PREFIX}{apikey.api_key}"
        pipe = redis_client.pipeline()

        # 存储API Key详细信息
        pipe.hset(key, "id", str(apikey.id))
        pipe.hset(key, "api_key", apikey.api_key)
        pipe.hset(key, "description", apikey.description or "")
        pipe.hset(key, "status", apikey.status)

        # 如果状态为1，添加到活跃集合
        if apikey.status == "1":
            pipe.sadd(API_KEY_SET, apikey.api_key)
        else:
            pipe.srem(API_KEY_SET, apikey.api_key)

        pipe.execute()
        logger.info(f"API Key Manager: Added API Key to Redis: {apikey.api_key}")

    except Exception as e:
        logger.error(f"API Key Manager: Failed to add API Key to Redis: {e}", exc_info=True)


async def update_apikey_in_redis(apikey: ApiKey):
    """
    更新Redis中的API Key

    Args:
        apikey: API Key对象
    """
    global redis_client
    if redis_client is None:
        logger.warning("API Key Manager: Redis not available, cannot update API Key")
        return

    try:
        key = f"{API_KEY_PREFIX}{apikey.api_key}"
        pipe = redis_client.pipeline()

        # 更新API Key详细信息
        pipe.hset(key, "id", str(apikey.id))
        pipe.hset(key, "api_key", apikey.api_key)
        pipe.hset(key, "description", apikey.description or "")
        pipe.hset(key, "status", apikey.status)

        # 根据状态更新活跃集合
        if apikey.status == "1":
            pipe.sadd(API_KEY_SET, apikey.api_key)
        else:
            pipe.srem(API_KEY_SET, apikey.api_key)

        pipe.execute()
        logger.info(f"API Key Manager: Updated API Key in Redis: {apikey.api_key}")

    except Exception as e:
        logger.error(f"API Key Manager: Failed to update API Key in Redis: {e}", exc_info=True)


async def delete_apikey_from_redis(api_key: str):
    """
    从Redis删除API Key

    Args:
        api_key: 要删除的API Key
    """
    global redis_client
    if redis_client is None:
        logger.warning("API Key Manager: Redis not available, cannot delete API Key")
        return

    try:
        key = f"{API_KEY_PREFIX}{api_key}"
        pipe = redis_client.pipeline()

        # 删除API Key详细信息
        pipe.delete(key)
        # 从活跃集合中移除
        pipe.srem(API_KEY_SET, api_key)

        pipe.execute()
        logger.info(f"API Key Manager: Deleted API Key from Redis: {api_key}")

    except Exception as e:
        logger.error(f"API Key Manager: Failed to delete API Key from Redis: {e}", exc_info=True)


def get_redis_client():
    """获取Redis客户端"""
    return redis_client
