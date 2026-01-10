# 账号池管理服务
import json
import time
import logging
import asyncio
import socket
from typing import Optional, Dict, List
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
                max_connections=200,            # 增加连接池大小
                decode_responses=True,
                socket_timeout=3,              # 减少超时时间
                socket_connect_timeout=3,       # 减少连接超时时间
                socket_keepalive=True,          # 启用保活
                socket_keepalive_options={
                    socket.TCP_KEEPIDLE: 1,
                    socket.TCP_KEEPINTVL: 3,
                    socket.TCP_KEEPCNT: 5
                },
                retry_on_timeout=False,         # 禁用超时重试，避免长时间阻塞
                health_check_interval=30
            )
            redis_client = redis.Redis(connection_pool=redis_pool)
            # 测试连接，设置较短的超时
            redis_client.ping()
            logger.info("Redis connected successfully")
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}, falling back to SQLite only mode")
            redis_client = None

async def initialize_pool():
    """初始化账号池（并发安全）"""
    init_redis()

    if redis_client is None:
        logger.warning("Redis not available, using SQLite only mode")
        return

    try:
        # 使用分布式锁防止重复初始化
        init_lock_key = "account_pool_init_lock"
        init_lock_acquired = redis_client.set(init_lock_key, "1", nx=True, ex=30)

        if not init_lock_acquired:
            logger.info("Account pool initialization already in progress, waiting...")
            # 等待初始化完成
            for _ in range(30):  # 最多等待3秒
                if not redis_client.exists(init_lock_key):
                    logger.info("Account pool initialization completed")
                    return
                await asyncio.sleep(0.1)
            logger.warning("Account pool initialization timeout, using existing pool")
            return

        try:
            # 检查是否已经初始化过
            existing_count = redis_client.zcard("available_accounts")
            if existing_count > 0:
                logger.info(f"Account pool already initialized with {existing_count} accounts")
                return

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
                    return

                # 使用批量操作提高性能
                pipe = redis_client.pipeline()
                pipe.delete("available_accounts")

                for account in accounts:
                    account_key = f"account_pool:{account.id}"
                    pipe.hset(account_key, "id", str(account.id))
                    pipe.hset(account_key, "account", account.account)
                    pipe.hset(account_key, "status", "1")
                    pipe.hset(account_key, "last_used", "0")
                    pipe.hset(account_key, "usage_count", "0")
                    pipe.hset(account_key, "error_count", "0")
                    pipe.hset(account_key, "health_score", "100")
                    pipe.zadd("available_accounts", {str(account.id): 100})

                pipe.execute()
                logger.info(f"Initialized pool with {len(accounts)} accounts")
        finally:
            # 释放初始化锁
            redis_client.delete(init_lock_key)

    except Exception as e:
        logger.error(f"Failed to initialize account pool: {e}")

async def get_available_account() -> Optional[Dict]:
    """获取可用账号"""
    if redis_client is None:
        return await _get_account_from_sqlite()

    try:
        account_id = redis_client.zrevrange("available_accounts", 0, 0)
        if not account_id:
            return None

        lock_key = f"account_lock:{account_id[0]}"
        if redis_client.exists(lock_key):
            return None

        redis_client.setex(lock_key, 10, str(time.time()))  # 减少锁定时间到10秒
        account_key = f"account_pool:{account_id[0]}"
        account_data = redis_client.hgetall(account_key)
        redis_client.hset(account_key, "status", "2")

        if account_data:
            logger.debug(f"Got account {account_id[0]} from pool")
            return account_data

        return None
    except Exception as e:
        logger.error(f"Failed to get account from pool: {e}")
        return await _get_account_from_sqlite()

async def release_account(account_id: int, success: bool = True, response_time: float = 0):
    """释放账号"""
    if redis_client is None:
        return

    try:
        account_key = f"account_pool:{account_id}"
        redis_client.delete(f"account_lock:{account_id}")

        pipe = redis_client.pipeline()
        pipe.hincrby(account_key, "usage_count", 1)
        pipe.hset(account_key, "last_used", str(time.time()))

        if success:
            pipe.hincrby(account_key, "health_score", 5)
            pipe.hset(account_key, "error_count", "0")
        else:
            pipe.hincrby(account_key, "error_count", 1)
            pipe.hincrby(account_key, "health_score", -10)

        pipe.execute()

        health_score = int(redis_client.hget(account_key, "health_score") or "0")
        health_score = max(0, min(100, health_score))
        redis_client.hset(account_key, "health_score", str(health_score))

        if health_score > 30:
            redis_client.zadd("available_accounts", {str(account_id): health_score})

        redis_client.hset(account_key, "status", "1")
        logger.debug(f"Released account {account_id}, success={success}, health_score={health_score}")
    except Exception as e:
        logger.error(f"Failed to release account {account_id}: {e}")

async def update_account(account_id: int, account_data: Dict):
    """更新账号信息"""
    try:
        async with AsyncSessionLocal() as db:
            stmt = select(Account).filter(Account.id == account_id)
            result = await db.execute(stmt)
            account = result.scalars().first()

            if account:
                for key, value in account_data.items():
                    setattr(account, key, value)
                await db.commit()
                logger.info(f"Updated account {account_id} in SQLite")

        if redis_client is not None:
            account_key = f"account_pool:{account_id}"

            if "account" in account_data:
                redis_client.hset(account_key, "account", json.dumps(account_data["account"]))

            if "status" in account_data:
                redis_client.hset(account_key, "status", str(account_data["status"]))

                if account_data["status"] == "1":
                    redis_client.zadd("available_accounts", {str(account_id): 100})
                else:
                    redis_client.zrem("available_accounts", str(account_id))

            logger.info(f"Updated account {account_id} in Redis")
    except Exception as e:
        logger.error(f"Failed to update account {account_id}: {e}")

async def get_pool_status() -> Dict:
    """获取账号池状态"""
    status = {
        "total_accounts": 0,
        "available_accounts": 0,
        "in_use_accounts": 0,
        "average_health_score": 0
    }

    if redis_client is None:
        return status

    try:
        status["available_accounts"] = redis_client.zcard("available_accounts")
        account_ids = redis_client.keys("account_pool:*")
        status["total_accounts"] = len(account_ids)

        if account_ids:
            total_health = 0
            for account_id in account_ids:
                health_score = int(redis_client.hget(account_id, "health_score") or "0")
                total_health += health_score

                if redis_client.hget(account_id, "status") == "2":
                    status["in_use_accounts"] += 1

            status["average_health_score"] = total_health / len(account_ids) if account_ids else 0

        return status
    except Exception as e:
        logger.error(f"Failed to get pool status: {e}")
        return status

async def health_check():
    """定期健康检查"""
    if redis_client is None:
        return

    try:
        account_ids = redis_client.keys("account_pool:*")

        for account_id in account_ids:
            account_key = account_id
            error_count = int(redis_client.hget(account_key, "error_count") or "0")
            health_score = int(redis_client.hget(account_key, "health_score") or "0")

            if error_count >= 5 or health_score < 20:
                redis_client.hset(account_key, "status", "0")
                redis_client.zrem("available_accounts", account_id.replace("account_pool:", ""))
                logger.warning(f"Disabled account {account_id} due to poor health")
            elif health_score > 50 and redis_client.hget(account_key, "status") == "0":
                redis_client.hset(account_key, "status", "1")
                redis_client.zadd("available_accounts", {account_id.replace("account_pool:", ""): health_score})
                logger.info(f"Re-enabled account {account_id}")

        logger.info("Health check completed")
    except Exception as e:
        logger.error(f"Health check failed: {e}")

async def create_account(account_data: str, status: str = "1", description: str = "") -> Optional[Account]:
    """创建账号"""
    # 处理account_data，移除id字段
    processed_account_data = account_data
    try:
        # 尝试解析为JSON
        data_dict = json.loads(account_data)
        if isinstance(data_dict, dict) and 'id' in data_dict:
            # 移除id字段
            data_dict_without_id = {k: v for k, v in data_dict.items() if k != 'id'}
            processed_account_data = json.dumps(data_dict_without_id)
    except (json.JSONDecodeError, TypeError):
        # 不是JSON格式，保持原样
        pass

    db_account = None
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with AsyncSessionLocal() as db:
                # 使用事务
                async with db.begin():
                    # 统计账号数量
                    count_stmt = select(func.count(Account.id))
                    count_result = await db.execute(count_stmt)
                    total_count = count_result.scalar() or 0

                    # 创建账号，使用数据库自动生成的ID
                    db_account = Account(
                        account=processed_account_data,
                        status=status,
                        description=description
                    )
                    db.add(db_account)
                    await db.flush()
                    await db.refresh(db_account)

                # 事务提交后更新Redis
                if redis_client is not None and status == "1":
                    try:
                        account_key = f"account_pool:{db_account.id}"
                        redis_client.hset(account_key, "id", str(db_account.id))
                        redis_client.hset(account_key, "account", account_data)
                        redis_client.hset(account_key, "status", "1")
                        redis_client.hset(account_key, "last_used", "0")
                        redis_client.hset(account_key, "usage_count", "0")
                        redis_client.hset(account_key, "error_count", "0")
                        redis_client.hset(account_key, "health_score", "100")
                        redis_client.zadd("available_accounts", {str(db_account.id): 100})
                    except Exception as redis_error:
                        logger.warning(f"Failed to update Redis for account {db_account.id}: {redis_error}")

                logger.info(f"Created account {db_account.id}")
                return db_account

        except Exception as e:
            logger.error(f"Failed to create account (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(0.1 * (attempt + 1))  # 指数退避
                continue
            raise

async def get_accounts(skip: int = 0, limit: int = 100, status: Optional[str] = None) -> List[Account]:
    """获取账号列表"""
    try:
        async with AsyncSessionLocal() as db:
            stmt = select(Account)
            if status is not None:
                stmt = stmt.filter(Account.status == status)
            stmt = stmt.order_by(Account.id.asc()).offset(skip)
            if limit is not None:
                stmt = stmt.limit(limit)
            result = await db.execute(stmt)
            return result.scalars().all()
    except Exception as e:
        logger.error(f"Failed to get accounts: {e}")
        return []

async def get_account(account_id: int) -> Optional[Account]:
    """获取单个账号"""
    try:
        async with AsyncSessionLocal() as db:
            stmt = select(Account).filter(Account.id == account_id)
            result = await db.execute(stmt)
            return result.scalars().first()
    except Exception as e:
        logger.error(f"Failed to get account {account_id}: {e}")
        return None

async def update_account(account_id: int, account_data: Optional[str] = None, status: Optional[str] = None, description: Optional[str] = None) -> Optional[Account]:
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

            logger.info(f"Updated account {account_id}")
            return account

    except Exception as e:
        logger.error(f"Failed to update account {account_id}: {e}")
        raise

async def delete_account(account_id: int) -> bool:
    """删除账号"""
    try:
        async with AsyncSessionLocal() as db:
            stmt = select(Account).filter(Account.id == account_id)
            result = await db.execute(stmt)
            account = result.scalars().first()

            if not account:
                return False

            await db.delete(account)
            await db.commit()

            if redis_client is not None:
                account_key = f"account_pool:{account_id}"
                redis_client.delete(account_key)
                redis_client.zrem("available_accounts", str(account_id))

            logger.info(f"Deleted account {account_id}")
            return True

    except Exception as e:
        logger.error(f"Failed to delete account {account_id}: {e}")
        return False

async def batch_delete_accounts(account_ids: List[int]) -> Dict:
    """批量删除账号"""
    try:
        async with AsyncSessionLocal() as db:
            stmt = select(Account).filter(Account.id.in_(account_ids))
            result = await db.execute(stmt)
            accounts = result.scalars().all()

            if not accounts:
                return {"deleted_count": 0, "message": "未找到要删除的账号"}

            for account in accounts:
                await db.delete(account)

            await db.commit()

            if redis_client is not None:
                pipe = redis_client.pipeline()
                for account_id in account_ids:
                    account_key = f"account_pool:{account_id}"
                    pipe.delete(account_key)
                    pipe.zrem("available_accounts", str(account_id))
                pipe.execute()

            logger.info(f"Deleted {len(accounts)} accounts")
            return {
                "deleted_count": len(accounts),
                "message": f"成功删除 {len(accounts)} 个账号"
            }

    except Exception as e:
        logger.error(f"Failed to batch delete accounts: {e}")
        raise

async def import_accounts(accounts: List[Dict]) -> Dict:
    """批量导入账号"""
    success_count = 0
    error_count = 0
    errors = []

    try:
        async with AsyncSessionLocal() as db:
            # 统计当前账号数量
            count_stmt = select(func.count(Account.id))
            count_result = await db.execute(count_stmt)
            total_count = count_result.scalar() or 0

            for idx, account_data in enumerate(accounts, 1):
                try:
                    # 移除account_data中的id字段，让数据库自动生成ID
                    if isinstance(account_data, dict) and 'id' in account_data:
                        account_data_without_id = {k: v for k, v in account_data.items() if k != 'id'}
                        account_str = json.dumps(account_data_without_id)
                    else:
                        account_str = json.dumps(account_data) if isinstance(account_data, dict) else str(account_data)

                    # 创建账号，id设置为当前总数+idx
                    db_account = Account(
                        id=total_count + idx,
                        account=account_str,
                        status="1",
                        description=f"导入自JSON - 第{idx}条"
                    )
                    db.add(db_account)
                    await db.commit()
                    success_count += 1

                    if redis_client is not None:
                        account_key = f"account_pool:{db_account.id}"
                        redis_client.hset(account_key, "id", str(db_account.id))
                        redis_client.hset(account_key, "account", account_str)
                        redis_client.hset(account_key, "status", "1")
                        redis_client.hset(account_key, "last_used", "0")
                        redis_client.hset(account_key, "usage_count", "0")
                        redis_client.hset(account_key, "error_count", "0")
                        redis_client.hset(account_key, "health_score", "100")
                        redis_client.zadd("available_accounts", {str(db_account.id): 100})

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
        logger.error(f"Failed to import accounts: {e}")
        raise

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

def close_redis():
    """关闭 Redis 连接"""
    global redis_client, redis_pool
    if redis_client:
        redis_client.close()
        redis_client = None
    if redis_pool:
        redis_pool.disconnect()
        redis_pool = None
