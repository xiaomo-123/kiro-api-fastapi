# 代理健康度检查服务
import logging
import asyncio
import time
from typing import Optional

logger = logging.getLogger(__name__)

# 延迟导入 redis_client 和 update_proxy，避免循环导入
_redis_client = None
_update_proxy = None

def _get_redis_client():
    """获取 Redis 客户端"""
    global _redis_client
    if _redis_client is None:
        from .proxy_pool import redis_client
        _redis_client = redis_client
    return _redis_client

def _get_update_proxy():
    """获取 update_proxy 函数"""
    global _update_proxy
    if _update_proxy is None:
        from .proxy_pool import update_proxy
        _update_proxy = update_proxy
    return _update_proxy


class ProxyHealthChecker:
    """代理健康度检查类 - 管理代理的健康状态和自动恢复"""

    # 类级别的锁，确保只有一个服务实例运行代理健康检查循环
    _proxy_health_check_lock = asyncio.Lock()
    _proxy_health_check_running = False

    def __init__(self, check_interval: int = 30):
        """初始化代理健康度检查器

        Args:
            check_interval: 健康度检查间隔（秒），默认30秒
        """
        self.proxy_health_check_task: Optional[asyncio.Task] = None
        self.proxy_health_check_interval: int = check_interval
        self.proxy_health_check_enabled: bool = True

    async def start_health_check_loop(self):
        """启动代理健康度检查循环任务"""
        if not self.proxy_health_check_enabled:
            logger.info('[ProxyHealthChecker] Proxy health check is disabled')
            return

        # 使用类级别的锁，确保只有一个服务实例运行代理健康检查循环
        if ProxyHealthChecker._proxy_health_check_running:
            logger.info('[ProxyHealthChecker] Health check loop already running')
            return

        if self.proxy_health_check_task is not None and not self.proxy_health_check_task.done():
            logger.info('[ProxyHealthChecker] Health check task already exists')
            return

        # 标记健康检查循环正在运行
        ProxyHealthChecker._proxy_health_check_running = True

        async def health_check_loop():
            """健康度检查循环"""
            while self.proxy_health_check_enabled:
                try:
                    recovered_count = await self.check_and_recover_proxies()
                    # if recovered_count > 0:
                    #     logger.info(f'[ProxyHealthChecker] Health check completed, recovered {recovered_count} proxies')
                    # else:
                    #     logger.info('[ProxyHealthChecker] Health check completed, no proxies recovered')

                    # 等待下一次检查
                    await asyncio.sleep(self.proxy_health_check_interval)
                except asyncio.CancelledError:
                    logger.info('[ProxyHealthChecker] Proxy health check task cancelled')
                    break
                except Exception as e:
                    logger.error(f'[ProxyHealthChecker] Error in health check loop: {e}')
                    await asyncio.sleep(self.proxy_health_check_interval)

        self.proxy_health_check_task = asyncio.create_task(health_check_loop())
        logger.info('[ProxyHealthChecker] Proxy health check loop started')

    async def stop_health_check(self):
        """停止代理健康度检查循环任务"""
        if self.proxy_health_check_task is None:
            return

        if not self.proxy_health_check_task.done():
            self.proxy_health_check_task.cancel()
            try:
                await self.proxy_health_check_task
            except asyncio.CancelledError:
                pass

        self.proxy_health_check_task = None
        ProxyHealthChecker._proxy_health_check_running = False
        logger.info('[ProxyHealthChecker] Proxy health check loop stopped')

    async def handle_proxy_error(self, proxy_id: Optional[int]) -> bool:
        """处理代理错误，增加错误计数，更新评分

        Args:
            proxy_id: 代理ID

        Returns:
            bool: 是否成功处理错误
        """
        try:
            if not proxy_id:
                return False

            redis_client = _get_redis_client()
            if redis_client is None:
                return False

            proxy_key = f"proxy_pool:{proxy_id}"
            current_score = int(redis_client.hget(proxy_key, "score") or "100")

            # 原子性地增加错误计数
            new_error_count = redis_client.hincrby(proxy_key, "error_count", 1)

            # 只在第一次错误时设置 last_error_time（初始化功能）
            if new_error_count == 1:
                redis_client.hset(proxy_key, "last_error_time", str(int(time.time())))

            # 计算新的评分：score = score - error_count
            new_score = max(0, current_score - new_error_count)
            redis_client.hset(proxy_key, "score", str(new_score))

            logger.debug(f'[ProxyHealthChecker] Proxy {proxy_id} error handled. New score: {new_score}, Error count: {new_error_count}')
            return True
        except Exception as e:
            logger.error(f'[ProxyHealthChecker] Failed to handle proxy error: {e}')
            return False

    async def disable_proxy(self, proxy_id: Optional[int] = None) -> bool:
        """禁用指定代理（从Redis代理池中标记为禁用）

        Args:
            proxy_id: 要禁用的代理ID

        Returns:
            bool: 是否成功禁用代理
        """
        try:
            if proxy_id is None:
                logger.warning('[ProxyHealthChecker] No proxy ID provided to disable')
                return False

            # 使用 update_proxy 将代理状态设置为 0（禁用）
            update_proxy = _get_update_proxy()
            await update_proxy(proxy_id, status="0")
            logger.info(f'[ProxyHealthChecker] Proxy {proxy_id} disabled')
            return True
        except Exception as e:
            logger.error(f'[ProxyHealthChecker] Failed to disable proxy {proxy_id}: {e}')
            return False

    async def enable_proxy(self, proxy_id: int) -> bool:
        """启用指定代理（从Redis代理池中标记为启用）

        Args:
            proxy_id: 要启用的代理ID

        Returns:
            bool: 是否成功启用代理
        """
        try:
            update_proxy = _get_update_proxy()
            await update_proxy(proxy_id, status="1")
            # logger.info(f'[ProxyHealthChecker] Proxy {proxy_id} enabled')
            return True
        except Exception as e:
            logger.error(f'[ProxyHealthChecker] Failed to enable proxy {proxy_id}: {e}')
            return False

    async def check_and_recover_proxies(self) -> int:
        """检查并恢复符合条件的代理

        Returns:
            int: 恢复的代理数量
        """
        try:
            redis_client = _get_redis_client()
            if redis_client is None:
                logger.warning('[ProxyHealthChecker] Redis client not available')
                return 0

            # 获取所有代理键
            proxy_keys = redis_client.keys("proxy_pool:*")
            current_time = int(time.time())
            recovered_count = 0

            for key in proxy_keys:
                # 获取代理ID
                proxy_id_str = key.split(":")[-1]
                try:
                    proxy_id = int(proxy_id_str)
                except ValueError:
                    logger.warning(f'[ProxyHealthChecker] Invalid proxy ID in key: {key}')
                    continue

                # 获取代理数据
                proxy_data = redis_client.hgetall(key)
                if not proxy_data:
                    continue

                # 获取评分和最后错误时间
                score = int(proxy_data.get("score", "100"))
                last_error_time = int(proxy_data.get("last_error_time", "0"))

                # 检查是否满足恢复条件
                if score > 50 and (current_time - last_error_time) > 60:
                    # 恢复代理
                    await self.enable_proxy(proxy_id)
                    # 重置最后错误时间
                    redis_client.hset(key, "last_error_time", "0")

                    # 计算新分数
                    new_score = score if score >= 100 else score + 1
                    redis_client.hset(key, "score", str(new_score))

                    recovered_count += 1
                    logger.debug(f'[ProxyHealthChecker] Recovered proxy {proxy_id}. New score: {new_score}')
                else:
                    # 不符合恢复条件，禁用代理
                    await self.disable_proxy(proxy_id)
                    logger.debug(f'[ProxyHealthChecker] Disabled proxy {proxy_id}. Score: {score}, Last error: {last_error_time}')

            return recovered_count
        except Exception as e:
            logger.error(f'[ProxyHealthChecker] Failed to check and recover proxies: {e}')
            return 0

    def set_check_interval(self, interval: int):
        """设置健康度检查间隔

        Args:
            interval: 检查间隔（秒）
        """
        self.proxy_health_check_interval = interval
        logger.info(f'[ProxyHealthChecker] Health check interval set to {interval} seconds')

    def enable_health_check(self):
        """启用健康度检查"""
        self.proxy_health_check_enabled = True
        logger.info('[ProxyHealthChecker] Health check enabled')

    def disable_health_check(self):
        """禁用健康度检查"""
        self.proxy_health_check_enabled = False
        logger.info('[ProxyHealthChecker] Health check disabled')
