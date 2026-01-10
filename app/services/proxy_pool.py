# 代理池管理服务
import json
import time
import logging
from typing import Optional, Dict, List
import redis
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func


from ..db.database import AsyncSessionLocal
from ..db.models import Proxy
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
                max_connections=500,            # 增加连接池大小到500，提高并发性能
                decode_responses=True,
                socket_timeout=5,              # 减少超时时间
                socket_connect_timeout=5,       # 减少连接超时时间
                retry_on_timeout=False         # 禁用超时重试，避免长时间阻塞
            )
            redis_client = redis.Redis(connection_pool=redis_pool)
            redis_client.ping()
            logger.info("Redis connected successfully")
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}, falling back to SQLite only mode")
            redis_client = None

async def initialize_pool():
    """初始化代理池"""
    init_redis()

    if redis_client is None:
        logger.warning("Redis not available, using SQLite only mode")
        return

    try:
        # 先清空所有代理数据
        proxy_keys = redis_client.keys("proxy_pool:*")
        if proxy_keys:
            redis_client.delete(*proxy_keys)
        redis_client.delete("available_proxies")
        logger.info(f"Cleared {len(proxy_keys) if proxy_keys else 0} existing proxies from pool")

        # 从数据库加载代理数据
        async with AsyncSessionLocal() as db:
            stmt = select(Proxy).filter(Proxy.status == "1")
            result = await db.execute(stmt)
            proxies = result.scalars().all()

            if not proxies:
                logger.warning("No enabled proxies found in database")
                return

            pipe = redis_client.pipeline()
            pipe.delete("available_proxies")

            for proxy in proxies:
                logger.info(f"代理加入:{proxy.proxy_url}:{proxy.proxy_port}")
                proxy_key = f"proxy_pool:{proxy.id}"
                pipe.hset(proxy_key, "id", str(proxy.id))
                pipe.hset(proxy_key, "proxy_type", proxy.proxy_type)
                pipe.hset(proxy_key, "proxy_url", proxy.proxy_url)
                pipe.hset(proxy_key, "proxy_port", str(proxy.proxy_port) if proxy.proxy_port else "")
                pipe.hset(proxy_key, "username", proxy.username or "")
                pipe.hset(proxy_key, "password", proxy.password or "")
                pipe.hset(proxy_key, "status", "1")
                pipe.hset(proxy_key, "last_used", "0")
                pipe.hset(proxy_key, "usage_count", "0")
                pipe.hset(proxy_key, "error_count", "0")
                pipe.hset(proxy_key, "score", "100")
                pipe.hset(proxy_key, "last_error_time", "0")
                pipe.zadd("available_proxies", {str(proxy.id): 100})

            pipe.execute()
            logger.info(f"Initialized pool with {len(proxies)} proxies")
    except Exception as e:
        logger.error(f"Failed to initialize proxy pool: {e}")

async def get_available_proxy() -> Optional[Dict]:
    """获取可用代理"""
    if redis_client is None:
        return await _get_proxy_from_sqlite()

    try:
        proxy_id = redis_client.zrevrange("available_proxies", 0, 0)
        if not proxy_id:
            return None

        lock_key = f"proxy_lock:{proxy_id[0]}"
        if redis_client.exists(lock_key):
            return None

        redis_client.setex(lock_key, 10, str(time.time()))  # 减少锁定时间到10秒
        proxy_key = f"proxy_pool:{proxy_id[0]}"
        proxy_data = redis_client.hgetall(proxy_key)
        
        # 检查代理是否可用
        if not proxy_data or proxy_data.get("status") != "1":
            logger.warning(f"Proxy {proxy_id[0]} is not available (status={proxy_data.get('status', 'N/A')})")
            redis_client.delete(lock_key)
            return None
        
        redis_client.hset(proxy_key, "status", "2")

        if proxy_data:
            logger.debug(f"Got proxy {proxy_id[0]} from pool")
            return proxy_data

        return None
    except Exception as e:
        logger.error(f"Failed to get proxy from pool: {e}")
        return await _get_proxy_from_sqlite()

async def release_proxy(proxy_id: int, success: bool = True, response_time: float = 0):
    """释放代理"""
    if redis_client is None:
        return

    try:
        proxy_key = f"proxy_pool:{proxy_id}"
        redis_client.delete(f"proxy_lock:{proxy_id}")

        pipe = redis_client.pipeline()
        pipe.hincrby(proxy_key, "usage_count", 1)
        pipe.hset(proxy_key, "last_used", str(time.time()))

        if success:
            pipe.hset(proxy_key, "error_count", "0")  # 成功时重置错误计数
        # 注意：score的计算在_switch_to_next_proxy中处理

        pipe.execute()

        # 获取当前score
        score = int(redis_client.hget(proxy_key, "score") or "100")
        score = max(0, min(100, score))
        redis_client.hset(proxy_key, "score", str(score))

        if score > 30:
            redis_client.zadd("available_proxies", {str(proxy_id): score})

        redis_client.hset(proxy_key, "status", "1")
        logger.debug(f"Released proxy {proxy_id}, success={success}, score={score}")
    except Exception as e:
        logger.error(f"Failed to release proxy {proxy_id}: {e}")

async def create_proxy(proxy_type: str, proxy_url: str, proxy_port: Optional[int] = None, 
                      username: Optional[str] = None, password: Optional[str] = None, 
                      status: str = "1") -> Optional[Proxy]:
    """创建代理"""
    try:
        async with AsyncSessionLocal() as db:
            # 统计代理数量
            count_stmt = select(func.count(Proxy.id))
            count_result = await db.execute(count_stmt)
            total_count = count_result.scalar() or 0
            
            # 创建代理，id设置为总数量+1
            db_proxy = Proxy(
                id=total_count + 1,
                proxy_type=proxy_type,
                proxy_url=proxy_url,
                proxy_port=proxy_port,
                username=username,
                password=password,
                status=status
            )
            db.add(db_proxy)
            await db.commit()
            await db.refresh(db_proxy)

            if redis_client is not None and status == "1":
                proxy_key = f"proxy_pool:{db_proxy.id}"
                redis_client.hset(proxy_key, "id", str(db_proxy.id))
                redis_client.hset(proxy_key, "proxy_type", proxy_type)
                redis_client.hset(proxy_key, "proxy_url", proxy_url)
                redis_client.hset(proxy_key, "proxy_port", str(proxy_port) if proxy_port else "")
                redis_client.hset(proxy_key, "username", username or "")
                redis_client.hset(proxy_key, "password", password or "")
                redis_client.hset(proxy_key, "status", "1")
                redis_client.hset(proxy_key, "last_used", "0")
                redis_client.hset(proxy_key, "usage_count", "0")
                redis_client.hset(proxy_key, "error_count", "0")
                redis_client.hset(proxy_key, "score", "100")
                redis_client.hset(proxy_key, "last_error_time", "0")
                redis_client.zadd("available_proxies", {str(db_proxy.id): 100})

            logger.info(f"Created proxy {db_proxy.id}")
            return db_proxy

    except Exception as e:
        logger.error(f"Failed to create proxy: {e}")
        raise

async def get_proxies(skip: int = 0, limit: int = 100) -> List[Proxy]:
    """获取代理列表"""
    try:
        async with AsyncSessionLocal() as db:
            stmt = select(Proxy).offset(skip).limit(limit)
            result = await db.execute(stmt)
            return result.scalars().all()
    except Exception as e:
        logger.error(f"Failed to get proxies: {e}")
        return []

async def get_proxy(proxy_id: int) -> Optional[Proxy]:
    """获取单个代理"""
    try:
        async with AsyncSessionLocal() as db:
            stmt = select(Proxy).filter(Proxy.id == proxy_id)
            result = await db.execute(stmt)
            return result.scalars().first()
    except Exception as e:
        logger.error(f"Failed to get proxy {proxy_id}: {e}")
        return None

async def update_proxy(proxy_id: int, proxy_type: Optional[str] = None, 
                      proxy_url: Optional[str] = None, proxy_port: Optional[int] = None,
                      username: Optional[str] = None, password: Optional[str] = None,
                      status: Optional[str] = None) -> Optional[Proxy]:
    """更新代理"""
    try:
        async with AsyncSessionLocal() as db:
            stmt = select(Proxy).filter(Proxy.id == proxy_id)
            result = await db.execute(stmt)
            proxy = result.scalars().first()
            
            if not proxy:
                return None

            # 如果代理状态为0（已禁用），则不更新
            if proxy.status == "0":
                logger.warning(f"Proxy {proxy_id} is disabled (status=0), skipping update")
                return proxy

            if proxy_type is not None:
                proxy.proxy_type = proxy_type
            if proxy_url is not None:
                proxy.proxy_url = proxy_url
            if proxy_port is not None:
                proxy.proxy_port = proxy_port
            if username is not None:
                proxy.username = username
            if password is not None:
                proxy.password = password
            if status is not None:
                proxy.status = status

            await db.commit()
            await db.refresh(proxy)
            
            if redis_client is not None:
                proxy_key = f"proxy_pool:{proxy_id}"
                
                # 检查代理是否存在于Redis中
                if not redis_client.exists(proxy_key):
                    # 如果Redis中不存在该代理，创建一个新的
                    redis_client.hset(proxy_key, "id", str(proxy_id))
                    redis_client.hset(proxy_key, "proxy_type", proxy.proxy_type)
                    redis_client.hset(proxy_key, "proxy_url", proxy.proxy_url)
                    redis_client.hset(proxy_key, "proxy_port", str(proxy.proxy_port) if proxy.proxy_port else "")
                    redis_client.hset(proxy_key, "username", proxy.username or "")
                    redis_client.hset(proxy_key, "password", proxy.password or "")
                    redis_client.hset(proxy_key, "status", proxy.status)
                    redis_client.hset(proxy_key, "last_used", "0")
                    redis_client.hset(proxy_key, "usage_count", "0")
                    redis_client.hset(proxy_key, "error_count", "0")
                    redis_client.hset(proxy_key, "score", "100")
                    redis_client.hset(proxy_key, "last_error_time", "0")
                    
                else:
                    
                    # 更新现有代理的Redis数据
                    if proxy_type is not None:
                        redis_client.hset(proxy_key, "proxy_type", proxy_type)

                    if proxy_url is not None:
                        redis_client.hset(proxy_key, "proxy_url", proxy_url)

                    if proxy_port is not None:
                        redis_client.hset(proxy_key, "proxy_port", str(proxy_port))

                    if username is not None:
                        redis_client.hset(proxy_key, "username", username)

                    if password is not None:
                        redis_client.hset(proxy_key, "password", password)

                    if status is not None:
                        # logger.info(f"Updating proxy {proxy_url} status to {status}")
                        redis_client.hset(proxy_key, "status", status)
                        # 如果代理被禁用，从available_proxies有序集合中移除
                        if status == "0":
                            redis_client.zrem("available_proxies", str(proxy_id))
                            logger.info(f"Removed proxy {proxy_id} from available_proxies")                                   
            return proxy

    except Exception as e:
        logger.error(f"Failed to update proxy {proxy_id}: {e}")
        raise

async def update_proxys(proxy_id: int, proxy_type: Optional[str] = None, 
                      proxy_url: Optional[str] = None, proxy_port: Optional[int] = None,
                      username: Optional[str] = None, password: Optional[str] = None,
                      status: Optional[str] = None) -> Optional[Proxy]:
    """更新代理"""
    try:
        async with AsyncSessionLocal() as db:
            stmt = select(Proxy).filter(Proxy.id == proxy_id)
            result = await db.execute(stmt)
            proxy = result.scalars().first()
            
            if not proxy:
                return None           

            if proxy_type is not None:
                proxy.proxy_type = proxy_type
            if proxy_url is not None:
                proxy.proxy_url = proxy_url
            if proxy_port is not None:
                proxy.proxy_port = proxy_port
            if username is not None:
                proxy.username = username
            if password is not None:
                proxy.password = password
            if status is not None:
                proxy.status = status

            await db.commit()
            await db.refresh(proxy)
            
            if redis_client is not None:
                proxy_key = f"proxy_pool:{proxy_id}"
                
                # 检查代理是否存在于Redis中
                if not redis_client.exists(proxy_key):
                    # 如果Redis中不存在该代理，创建一个新的
                    redis_client.hset(proxy_key, "id", str(proxy_id))
                    redis_client.hset(proxy_key, "proxy_type", proxy.proxy_type)
                    redis_client.hset(proxy_key, "proxy_url", proxy.proxy_url)
                    redis_client.hset(proxy_key, "proxy_port", str(proxy.proxy_port) if proxy.proxy_port else "")
                    redis_client.hset(proxy_key, "username", proxy.username or "")
                    redis_client.hset(proxy_key, "password", proxy.password or "")
                    redis_client.hset(proxy_key, "status", proxy.status)
                    redis_client.hset(proxy_key, "last_used", "0")
                    redis_client.hset(proxy_key, "usage_count", "0")
                    redis_client.hset(proxy_key, "error_count", "0")
                    redis_client.hset(proxy_key, "score", "100")
                    redis_client.hset(proxy_key, "last_error_time", "0")
                    
                else:
                    
                    # 更新现有代理的Redis数据
                    if proxy_type is not None:
                        redis_client.hset(proxy_key, "proxy_type", proxy_type)

                    if proxy_url is not None:
                        redis_client.hset(proxy_key, "proxy_url", proxy_url)

                    if proxy_port is not None:
                        redis_client.hset(proxy_key, "proxy_port", str(proxy_port))

                    if username is not None:
                        redis_client.hset(proxy_key, "username", username)

                    if password is not None:
                        redis_client.hset(proxy_key, "password", password)

                    if status is not None:
                        logger.info(f"Updating proxy {proxy_key} status to {status}")
                        redis_client.hset(proxy_key, "status", status)
                        # 如果代理被禁用，从available_proxies有序集合中移除
                        if status == "0":
                            redis_client.zrem("available_proxies", str(proxy_id))
                            logger.info(f"Removed proxy {proxy_id} from available_proxies")
                        

            logger.info(f"Updated proxy {proxy_id}")
            return proxy

    except Exception as e:
        logger.error(f"Failed to update proxy {proxy_id}: {e}")
        raise

async def delete_proxy(proxy_id: int) -> bool:
    """删除代理"""
    try:
        async with AsyncSessionLocal() as db:
            stmt = select(Proxy).filter(Proxy.id == proxy_id)
            result = await db.execute(stmt)
            proxy = result.scalars().first()

            if not proxy:
                return False

            await db.delete(proxy)
            await db.commit()

            if redis_client is not None:
                proxy_key = f"proxy_pool:{proxy_id}"
                redis_client.delete(proxy_key)
                redis_client.zrem("available_proxies", str(proxy_id))

            logger.info(f"Deleted proxy {proxy_id}")
            return True

    except Exception as e:
        logger.error(f"Failed to delete proxy {proxy_id}: {e}")
        return False

async def batch_delete_proxies(proxy_ids: List[int]) -> Dict:
    """批量删除代理"""
    try:
        async with AsyncSessionLocal() as db:
            stmt = select(Proxy).filter(Proxy.id.in_(proxy_ids))
            result = await db.execute(stmt)
            proxies = result.scalars().all()

            if not proxies:
                return {"deleted_count": 0, "message": "未找到要删除的代理"}

            for proxy in proxies:
                await db.delete(proxy)

            await db.commit()

            if redis_client is not None:
                pipe = redis_client.pipeline()
                for proxy_id in proxy_ids:
                    proxy_key = f"proxy_pool:{proxy_id}"
                    pipe.delete(proxy_key)
                    pipe.zrem("available_proxies", str(proxy_id))
                pipe.execute()

            logger.info(f"Deleted {len(proxies)} proxies")
            return {
                "deleted_count": len(proxies),
                "message": f"成功删除 {len(proxies)} 个代理"
            }

    except Exception as e:
        logger.error(f"Failed to batch delete proxies: {e}")
        raise

async def import_proxies(proxies: List[Dict]) -> Dict:
    """批量导入代理"""
    success_count = 0
    error_count = 0
    errors = []

    try:
        async with AsyncSessionLocal() as db:
            # 统计当前代理数量
            count_stmt = select(func.count(Proxy.id))
            count_result = await db.execute(count_stmt)
            total_count = count_result.scalar() or 0
            
            for idx, proxy_data in enumerate(proxies, 1):
                try:
                    proxy_type = proxy_data.get("proxy_type", "http")
                    proxy_url = proxy_data.get("proxy_url", "")
                    proxy_port = proxy_data.get("proxy_port")
                    username = proxy_data.get("username")
                    password = proxy_data.get("password")

                    # 创建代理，id设置为当前总数+idx
                    db_proxy = Proxy(
                        id=total_count + idx,
                        proxy_type=proxy_type,
                        proxy_url=proxy_url,
                        proxy_port=proxy_port,
                        username=username,
                        password=password,
                        status="1"
                    )
                    db.add(db_proxy)
                    await db.commit()
                    success_count += 1

                    if redis_client is not None:
                        proxy_key = f"proxy_pool:{db_proxy.id}"
                        redis_client.hset(proxy_key, "id", str(db_proxy.id))
                        redis_client.hset(proxy_key, "proxy_type", proxy_type)
                        redis_client.hset(proxy_key, "proxy_url", proxy_url)
                        redis_client.hset(proxy_key, "proxy_port", str(proxy_port) if proxy_port else "")
                        redis_client.hset(proxy_key, "username", username or "")
                        redis_client.hset(proxy_key, "password", password or "")
                        redis_client.hset(proxy_key, "status", "1")
                        redis_client.hset(proxy_key, "last_used", "0")
                        redis_client.hset(proxy_key, "usage_count", "0")
                        redis_client.hset(proxy_key, "error_count", "0")
                        redis_client.hset(proxy_key, "health_score", "100")
                        redis_client.zadd("available_proxies", {str(db_proxy.id): 100})

                except Exception as e:
                    error_count += 1
                    errors.append(f"第{idx}条导入失败: {str(e)}")
                    await db.rollback()

        return {
            "message": f"导入完成，成功{success_count}条，失败{error_count}条",
            "success_count": success_count,
            "error_count": error_count,
            "errors": errors
        }

    except Exception as e:
        logger.error(f"Failed to import proxies: {e}")
        raise

async def _get_proxy_from_sqlite() -> Optional[Dict]:
    """从 SQLite 获取代理"""
    try:
        async with AsyncSessionLocal() as db:
            stmt = select(Proxy).filter(Proxy.status == "1").order_by(Proxy.id)
            result = await db.execute(stmt)
            proxy = result.scalars().first()

            if proxy:
                return {
                    "id": str(proxy.id),
                    "proxy_type": proxy.proxy_type,
                    "proxy_url": proxy.proxy_url,
                    "proxy_port": str(proxy.proxy_port) if proxy.proxy_port else "",
                    "username": proxy.username or "",
                    "password": proxy.password or "",
                    "status": "1",
                    "last_used": "0",
                    "usage_count": "0",
                    "error_count": "0",
                    "health_score": "100"
                }

            return None
    except Exception as e:
        logger.error(f"Failed to get proxy from SQLite: {e}")
        return None

async def get_proxy_pool_stats() -> Dict:
    """获取代理池统计信息"""
    try:
        if redis_client is None:
            return {"error": "Redis not connected"}

        # 获取所有代理
        proxy_keys = redis_client.keys("proxy_pool:*")
        total_proxies = len(proxy_keys)

        # 统计代理状态
        status_stats = {"0": 0, "1": 0}  # 0: 禁用, 1: 启用
        health_scores = []
        error_counts = []

        for proxy_key in proxy_keys:
            proxy_data = redis_client.hgetall(proxy_key)
            if proxy_data:
                status = proxy_data.get("status", "1")
                if status in status_stats:
                    status_stats[status] += 1

                health_score = int(proxy_data.get("score", "100"))
                health_scores.append(health_score)

                error_count = int(proxy_data.get("error_count", "0"))
                error_counts.append(error_count)

        # 计算平均健康分数和错误次数
        avg_health_score = sum(health_scores) / len(health_scores) if health_scores else 0
        avg_error_count = sum(error_counts) / len(error_counts) if error_counts else 0

        return {
            "total_proxies": total_proxies,
            "enabled_proxies": status_stats["1"],
            "disabled_proxies": status_stats["0"],
            "avg_health_score": round(avg_health_score, 2),
            "avg_error_count": round(avg_error_count, 2),
            "status_stats": status_stats
        }
    except Exception as e:
        logger.error(f"Failed to get proxy pool stats: {e}")
        return {"error": str(e)}

def close_redis():
    """关闭 Redis 连接"""
    global redis_client, redis_pool
    if redis_client:
        redis_client.close()
        redis_client = None
    if redis_pool:
        redis_pool.disconnect()
        redis_pool = None
