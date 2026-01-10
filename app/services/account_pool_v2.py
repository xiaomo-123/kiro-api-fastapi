# 账号池管理服务 V2 - 高并发优化版
import json
import time
import logging
import asyncio
from typing import Optional, Dict
import redis
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func


from ..db.database import AsyncSessionLocal
from ..db.models import Account
from ..config import settings

logger = logging.getLogger(__name__)

# Redis 连接池
redis_pool = None
redis_client = None

# 账号重载器实例
account_pool_reloader = None

class AccountPoolReloader:
    """账号池重载器，定期同步数据库和Redis中的账号数据"""

    def __init__(self, reload_interval: int = 10):
        """
        初始化账号池重载器

        Args:
            reload_interval: 重载间隔（秒），默认60秒
        """
        self.reload_interval = reload_interval
        self._reload_task: Optional[asyncio.Task] = None
        self._is_running = False

    async def start(self):
        """启动账号重载任务"""
        if self._is_running:
            logger.info("Account pool reloader is already running")
            return

        self._is_running = True
        self._reload_task = asyncio.create_task(self._reload_loop())        

    async def stop(self):
        """停止账号重载任务"""
        if not self._is_running:
            return

        self._is_running = False
        if self._reload_task:
            self._reload_task.cancel()
            try:
                await self._reload_task
            except asyncio.CancelledError:
                pass
        logger.info("Account pool reloader stopped")

    async def _reload_loop(self):
        """重载循环"""
        
        while self._is_running:
            try:
                
                await self.reload_accounts()
                # logger.info(f"重载账号池 {self.reload_interval}s")
                await asyncio.sleep(self.reload_interval)
            except asyncio.CancelledError:
                logger.info("Account reload loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in account reload loop: {e}", exc_info=True)
                # 即使出错也继续运行，避免循环终止
                await asyncio.sleep(self.reload_interval)
        logger.info("Account reload loop stopped")

    async def reload_accounts(self):
        """
        重载账号池：
        1. 遍历数据库中的账号
        2. 如果Redis中不存在该账号ID，则添加
        3. 如果Redis中账号状态为0，则从Redis和数据库中清除
        """
        global redis_client

        if redis_client is None:
            logger.warning("Redis not available, skipping account reload")
            return

        try:
            # 使用分布式锁防止并发重载
            reload_lock_key = "account_pool_reload_lock"
            lock_acquired = redis_client.set(reload_lock_key, "1", nx=True, ex=10)

            if not lock_acquired:
                logger.debug("Account pool reload already in progress, skipping")
                return

            try:
                logger.debug("Starting account pool reload...")

                # 从数据库加载所有账号
                async with AsyncSessionLocal() as db:
                    stmt = select(Account)
                    result = await db.execute(stmt)
                    db_accounts = result.scalars().all()

                    # 获取Redis中所有账号
                    redis_account_keys = redis_client.keys("account_pool:*")
                    redis_account_ids = set()
                    redis_account_status = {}
                    for key in redis_account_keys:
                        account_id = key.split(":")[-1]
                        redis_account_ids.add(account_id)
                        redis_account_status[account_id] = redis_client.hget(key, "status")

                    added_count = 0
                    removed_count = 0

                    # 创建批量操作管道
                    pipe = redis_client.pipeline()

                    # 遍历数据库账号
                    for db_account in db_accounts:
                        account_id_str = str(db_account.id)
                        account_key = f"account_pool:{account_id_str}"

                        # 检查Redis中是否存在该账号
                        if account_id_str not in redis_account_ids:
                            # Redis中不存在，添加新账号
                            if db_account.status == "1":
                                pipe.hset(account_key, "id", account_id_str)
                                pipe.hset(account_key, "account", db_account.account)
                                pipe.hset(account_key, "status", "1")
                                pipe.hset(account_key, "last_used_at", "0")
                                pipe.hset(account_key, "usage_count", "0")
                                pipe.hset(account_key, "error_count", "0")
                                pipe.hset(account_key, "health_score", "100")
                                pipe.hset(account_key, "first_used_at", "0")
                                pipe.zadd("available_accounts", {account_id_str: 100})
                                added_count += 1
                                logger.info(f"Added new account {account_id_str} to pool")
                        else:
                            # Redis中已存在，检查状态
                            redis_status = redis_account_status.get(account_id_str)

                            # 如果数据库中状态为0，或者Redis中状态为0，则从Redis中清除
                            if db_account.status == "0" or redis_status == "0":
                                pipe.delete(account_key)
                                pipe.zrem("available_accounts", account_id_str)
                                pipe.delete(f"account_lock:{account_id_str}")
                                removed_count += 1
                                logger.info(f"Removed account {account_id_str} (db_status={db_account.status}, redis_status={redis_status}) from pool")

                    # 检查Redis中是否存在数据库中不存在的账号
                    for redis_id in redis_account_ids:
                        if not any(str(db_account.id) == redis_id for db_account in db_accounts):
                            # 数据库中不存在，从Redis中清除
                            account_key = f"account_pool:{redis_id}"
                            pipe.delete(account_key)
                            pipe.zrem("available_accounts", redis_id)
                            pipe.delete(f"account_lock:{redis_id}")
                            removed_count += 1
                            logger.info(f"Removed account {redis_id} (not in database) from pool")

                    # 执行批量操作
                    if added_count > 0 or removed_count > 0:
                        pipe.execute()
                        await db.commit()
                        logger.info(f"Account pool reload completed: added {added_count}, removed {removed_count}")
                    else:
                        # 即使没有变化也要执行管道，以确保所有操作都被正确处理
                        pipe.execute()
                        logger.debug("Account pool reload completed: no changes")

            finally:
                # 释放重载锁
                redis_client.delete(reload_lock_key)

        except Exception as e:
            logger.error(f"Failed to reload accounts: {e}", exc_info=True)

def init_redis():
    """初始化 Redis 连接"""
    global redis_pool, redis_client
    if redis_client is None:
        try:
            logger.info(f"Attempting to connect to Redis at {getattr(settings, 'REDIS_HOST', 'localhost')}:{getattr(settings, 'REDIS_PORT', 6379)}")
            redis_pool = redis.ConnectionPool(
                host=getattr(settings, "REDIS_HOST", "localhost"),
                port=getattr(settings, "REDIS_PORT", 6379),
                db=getattr(settings, "REDIS_DB", 0),
                password=getattr(settings, "REDIS_PASSWORD", None),
                max_connections=500,            # 增加连接池大小到500，提高并发性能
                decode_responses=True,
                socket_timeout=3,              # 减少超时时间
                socket_connect_timeout=3,       # 减少连接超时时间
                retry_on_timeout=False         # 禁用超时重试，避免长时间阻塞
            )
            redis_client = redis.Redis(connection_pool=redis_pool)
            redis_client.ping()

            # 测试写入权限
            test_key = "redis_write_test"
            redis_client.set(test_key, "1", ex=1)
            redis_client.delete(test_key)

            logger.info("Redis connected successfully with write permission")
        except redis.exceptions.ReadOnlyError:
            logger.error("Redis is in read-only mode. Please check Redis configuration and ensure you're connecting to the master node, not a replica.")
            redis_client = None
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}, falling back to SQLite only mode")
            redis_client = None

async def initialize_pool():
    """初始化账号池（并发安全）"""
    global account_pool_reloader

    init_redis()

    if redis_client is None:
        logger.warning("Redis not available, using SQLite only mode")
        return

    try:
        # 使用分布式锁防止重复初始化
        init_lock_key = "account_pool_init_lock_v2"
        init_lock_acquired = redis_client.set(init_lock_key, "1", nx=True, ex=30)

        if not init_lock_acquired:
            
            # 等待初始化完成
            for _ in range(30):  # 最多等待3秒
                if not redis_client.exists(init_lock_key):                   
                    return
                await asyncio.sleep(0.1)
            
            return

        try:
            # 先清空所有账号数据
            account_keys = redis_client.keys("account_pool:*")
            if account_keys:
                redis_client.delete(*account_keys)
            redis_client.delete("available_accounts")
            logger.info(f"Cleared {len(account_keys) if account_keys else 0} existing accounts from pool")

            # 从数据库加载账号数据
            async with AsyncSessionLocal() as db:
                    stmt = select(Account).filter(Account.status == "1")
                    result = await db.execute(stmt)
                    accounts = result.scalars().all()

                    

                    if not accounts:
                        logger.warning("No enabled accounts found in database")
                        

                    # 记录账号详情（不包含敏感信息）
                    # for account in accounts:
                    #     logger.info(f"Account ID: {account.id}, Status: {account.status}, Description: {account.description}")

                    # 使用批量操作提高性能
                    pipe = redis_client.pipeline()
                    pipe.delete("available_accounts")

                    for account in accounts:
                        account_key = f"account_pool:{account.id}"
                        pipe.hset(account_key, "id", str(account.id))
                        pipe.hset(account_key, "account", account.account)
                        pipe.hset(account_key, "status", "1")
                        pipe.hset(account_key, "last_used_at", "0")
                        pipe.hset(account_key, "usage_count", "0")
                        pipe.hset(account_key, "error_count", "0")
                        pipe.hset(account_key, "health_score", "100")
                        pipe.hset(account_key, "first_used_at", "0")
                        pipe.zadd("available_accounts", {str(account.id): 100})

                    pipe.execute()
                    logger.info(f"Initialized pool with {len(accounts)} accounts")
        finally:
            # 释放初始化锁
            redis_client.delete(init_lock_key)

    except Exception as e:
        logger.error(f"Failed to initialize account pool: {e}")

async def get_available_account() -> Optional[Dict]:
    """获取可用账号（优先选择并发数低的新账号）"""
    if redis_client is None:
        return await _get_account_from_sqlite()

    try:
        # 导入并发控制管理器
        from .account_concurrency_manager import acquire_account, get_account_concurrency

        # 获取最大并发数配置
        max_concurrent = getattr(settings, "ACCOUNT_MAX_CONCURRENT", 5)

        # 获取所有可用账号
        account_ids = redis_client.zrange("available_accounts", 0, -1)
        if not account_ids:
            return None

        # 第一轮：优先选择新账号（得分高）
        # 收集所有账号的详细信息
        account_info = []
        for acc_id in account_ids:
            try:
                account_key = f"account_pool:{acc_id}"
                account_data = redis_client.hgetall(account_key)

                # 只选择状态为可用(1)的账号
                if account_data.get("status") != "1":
                    continue

                # 获取并发数
                concurrency = await get_account_concurrency(int(acc_id))

                # 判断是否为新账号
                first_used = account_data.get("first_used_at")
                use_count = int(account_data.get("usage_count", 0))
                error_count = int(account_data.get("error_count", 0))
                is_new = False

                if first_used:
                    first_used_time = float(first_used)
                    hours_since_first = (time.time() - first_used_time) / 3600
                    if hours_since_first < 24 and use_count < 10 and error_count < 3:
                        is_new = True

                # 计算账号得分（优先选择新账号，然后选择并发数低的）
                score = 0
                if is_new:
                    score += 100  # 新账号优先
                score += (max_concurrent - concurrency)  # 并发数低的优先

                account_info.append({
                    'account_id': acc_id,
                    'account_data': account_data,
                    'concurrency': concurrency,
                    'is_new': is_new,
                    'score': score
                })
            except Exception as e:
                logger.warning(f"Failed to get info for account {acc_id}: {e}")
                continue

        # 按得分排序
        account_info.sort(key=lambda x: x['score'], reverse=True)
        # logger.debug(f"Account ranking: {[(a['account_id'], a['score'], a['is_new']) for a in account_info]}")

        # 第一轮：尝试从得分高的账号开始
        for info in account_info:
            account_id = info['account_id']
            account_data = info['account_data']
            concurrency = info['concurrency']
            lock_key = f"account_lock:{account_id}"

            # 尝试获取锁
            if redis_client.set(lock_key, str(time.time()), nx=True, ex=5):
                account_key = f"account_pool:{account_id}"

                # 验证账号状态
                if not account_data:
                    redis_client.delete(lock_key)
                    redis_client.zrem("available_accounts", account_id)
                    continue

                if account_data.get("status") != "1":
                    logger.warning(f"Account {account_id} status is {account_data.get('status')}, skipping")
                    redis_client.delete(lock_key)
                    continue

                # 检查账号并发限制
                acquired = await acquire_account(int(account_id), max_concurrent)
                if not acquired:
                    logger.debug(f"Account {account_id} concurrency limit reached (current: {concurrency}), trying next account")
                    redis_client.delete(lock_key)
                    continue

                # 成功获取账号
                redis_client.hset(account_key, "status", "2")
                # logger.debug(f"Got account {account_id} from pool (new={info['is_new']}, concurrency: {concurrency})")
                return account_data

        # 第二轮：如果所有账号都达到并发限制，使用轮询方式选择一个账号（即使达到限制也使用）
        logger.warning("All accounts reached concurrency limit, selecting account by round-robin")
        current_time = int(time.time() * 1000)
        account_index = current_time % len(account_ids)
        account_id = account_ids[account_index]

        lock_key = f"account_lock:{account_id}"
        if redis_client.set(lock_key, str(time.time()), nx=True, ex=5):
            account_key = f"account_pool:{account_id}"
            account_data = redis_client.hgetall(account_key)

            if not account_data:
                redis_client.delete(lock_key)
                redis_client.zrem("available_accounts", account_id)
                return None

            # 检查账号状态，如果状态不是1，则跳过
            if account_data.get("status") != "1":
                
                redis_client.delete(lock_key)
                return None

            # 第二轮：即使达到并发限制也使用，不检查 acquire_account
            redis_client.hset(account_key, "status", "2")
            logger.warning(f"Using account {account_id} even though concurrency limit may be reached")
            return account_data

        return None

    except Exception as e:
        logger.error(f"Failed to get account from pool: {e}")
        return await _get_account_from_sqlite()

async def update_account(account_id: int, account_data: Optional[str] = None, status: Optional[str] = None, description: Optional[str] = None) -> Optional[Dict]:
    """更新账号"""
    try:
        async with AsyncSessionLocal() as db:
            stmt = select(Account).filter(Account.id == account_id)
            result = await db.execute(stmt)
            account = result.scalars().first()

            if not account:
                return None

            if account_data is not None:
                account.account = account_data
            if status is not None:
                account.status = status
            if description is not None:
                account.description = description

            await db.commit()
            await db.refresh(account)

            if redis_client is not None:
                account_key = f"account_pool:{account_id}"

                if account_data is not None:
                    redis_client.hset(account_key, "account", account_data)

                if status is not None:
                    redis_client.hset(account_key, "status", status)

                    if status == "1":
                        redis_client.zadd("available_accounts", {str(account_id): 100})
                    else:
                        redis_client.zrem("available_accounts", str(account_id))

                if description is not None:
                    redis_client.hset(account_key, "description", description)

            
            return {
                "id": str(account.id),
                "account": account.account,
                "status": account.status,
                "description": account.description
            }
    except Exception as e:
        logger.error(f"Failed to update account {account_id}: {e}")
        return None


async def release_account(account_id: int, success: bool = True, response_time: float = 0):
    """释放账号
    
    Args:
        account_id: 账号ID
        success: 请求是否成功
        response_time: 响应时间（秒）
    """
    if redis_client is None:
        return

    try:
        # 释放并发计数
        from .account_concurrency_manager import release_account as release_concurrency
        await release_concurrency(account_id)

        account_key = f"account_pool:{account_id}"        
        # 如果请求成功，将账号状态重置为可用（"1"）
        if success:
            redis_client.hset(account_key, "status", "1")
            # 更新账号使用信息
            redis_client.hincrby(account_key, "usage_count", 1)
            redis_client.hset(account_key, "last_used_at", str(time.time()))
            # 如果是首次使用，记录首次使用时间
            first_used_at = redis_client.hget(account_key, "first_used_at")
            if first_used_at == "0":
                redis_client.hset(account_key, "first_used_at", str(time.time()))
        else:
            # 请求失败，增加错误计数
            redis_client.hincrby(account_key, "error_count", 1)
            redis_client.hset(account_key, "last_error_time", str(time.time()))

            # 将Redis中账号状态设置为0
            redis_client.hset(account_key, "status", "0")
            redis_client.zrem("available_accounts", str(account_id))

            # 将数据库中账号状态设置为0
            try:
                async with AsyncSessionLocal() as db:
                    stmt = select(Account).filter(Account.id == account_id)
                    result = await db.execute(stmt)
                    account = result.scalars().first()

                    if account:
                        account.status = "0"
                        await db.commit()
                        logger.info(f"Updated account {account_id} status to 0 in Redis and database due to failure")
            except Exception as db_error:
                logger.error(f"Failed to update account {account_id} status in database: {db_error}")      
        
    except Exception as e:
        logger.error(f"Failed to release account {account_id}: {e}")

async def _get_account_from_sqlite() -> Optional[Dict]:
    """从 SQLite 获取账号"""
    try:
        async with AsyncSessionLocal() as db:
            stmt = select(Account).filter(Account.status == "1").order_by(Account.id)
            result = await db.execute(stmt)
            account = result.scalars().first()

            if account:
                return {
                    "id": str(account.id),
                    "account": account.account,
                    "status": "1",
                    "last_used": "0",
                    "usage_count": "0",
                    "error_count": "0",
                    "health_score": "100"
                }

            return None
    except Exception as e:
        logger.error(f"Failed to get account from SQLite: {e}")
        return None

async def stop_reloader():
    """停止账号重载器"""
    global account_pool_reloader
    if account_pool_reloader is not None:
        await account_pool_reloader.stop()
        account_pool_reloader = None
        logger.info("Account pool reloader stopped")

async def get_pool_stats() -> Dict:
    """获取账号池统计信息"""
    try:
        if redis_client is None:
            return {"error": "Redis not connected"}

        # 获取所有账号
        account_keys = redis_client.keys("account_pool:*")
        total_accounts = len(account_keys)

        # 获取可用账号
        available_accounts = redis_client.zrange("available_accounts", 0, -1)
        available_count = len(available_accounts)

        # 统计账号状态
        status_stats = {"1": 0, "2": 0}  # 1: 可用, 2: 使用中
        health_scores = []

        for account_key in account_keys:
            account_data = redis_client.hgetall(account_key)
            if account_data:
                status = account_data.get("status", "1")
                if status in status_stats:
                    status_stats[status] += 1

                health_score = int(account_data.get("health_score", "100"))
                health_scores.append(health_score)

        # 计算平均健康分数
        avg_health_score = sum(health_scores) / len(health_scores) if health_scores else 0

        return {
            "total_accounts": total_accounts,
            "available_accounts": available_count,
            "in_use_accounts": status_stats["2"],
            "avg_health_score": round(avg_health_score, 2),
            "status_stats": status_stats
        }
    except Exception as e:
        logger.error(f"Failed to get pool stats: {e}")
        return {"error": str(e)}

def close_redis():
    """关闭 Redis 连接"""
    global redis_client, redis_pool
    if redis_client:
        redis_client.close()
        redis_client = None
    if redis_pool:
        redis_pool.disconnect()
