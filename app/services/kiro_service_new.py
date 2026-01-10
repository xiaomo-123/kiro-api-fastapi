# Kiro API 服务层 - 主入口（支持并发）
import logging
from typing import AsyncGenerator, Dict, Optional
import asyncio
import time

from .kiro_base import KiroBaseService
from .kiro_stream import KiroStreamService
from .kiro_nonstream import KiroNonStreamService

logger = logging.getLogger(__name__)


class KiroApiService(KiroBaseService):
    """Kiro API 服务类 - 整合流式和非流式功能"""

    # 类级别的锁，防止并发初始化
    _init_lock = asyncio.Lock()

    def __init__(self):
        """初始化服务"""
        super().__init__()

        # 导入流式和非流式服务的方法
        from .kiro_stream import KiroStreamService
        from .kiro_nonstream import KiroNonStreamService

        # 复制流式和非流式服务的方法（包括私有方法）
        for name in dir(KiroStreamService):
            if not name.startswith('__') and callable(getattr(KiroStreamService, name)):
                setattr(self, name, getattr(KiroStreamService, name).__get__(self, type(self)))

        for name in dir(KiroNonStreamService):
            if not name.startswith('__') and callable(getattr(KiroNonStreamService, name)):
                setattr(self, name, getattr(KiroNonStreamService, name).__get__(self, type(self)))


# 服务池管理
class KiroServicePool:
    """Kiro服务池 - 支持并发请求"""

    def __init__(self, pool_size: int = 10):
        """初始化服务池

        Args:
            pool_size: 服务池大小
        """
        self.pool_size = pool_size
        self.services: list[KiroApiService] = []
        self.current_index = 0
        self.lock = asyncio.Lock()
        # 服务实例状态跟踪
        self.service_stats = {}  # {service_index: {'active_requests': int, 'last_used': float}}
        self.stats_lock = asyncio.Lock()

    async def initialize(self):
        """初始化服务池"""
        async with KiroApiService._init_lock:
            if self.services:
                return  # 已经初始化过

            #logger.info(f"Initializing Kiro service pool with {self.pool_size} services")

            # 先初始化一个临时服务实例以获取代理池信息
            temp_service = KiroApiService()
            await temp_service._initialize_proxy_pool()
            proxy_count = len(temp_service.proxy_pool) if temp_service.proxy_pool else 0
            logger.info(f"Found {proxy_count} available proxies in pool")

            # 创建多个服务实例，根据代理数量分配代理索引
            for i in range(self.pool_size):
                service = KiroApiService()
                # 根据代理数量分配代理索引，避免多个实例使用相同的代理
                # 如果代理数量为0，所有实例都使用索引0（无代理）
                # 如果代理数量大于0，使用取模运算分配代理索引
                service.current_proxy_index = i % max(proxy_count, 1)
                await service.initialize()
                self.services.append(service)
                proxy_index = service.current_proxy_index if proxy_count > 0 else "none"
                logger.info(f"Initialized Kiro service {i+1}/{self.pool_size} with proxy index {proxy_index}")

            #logger.info(f"Kiro service pool initialized with {len(self.services)} services")

    async def get_service(self) -> KiroApiService:
        """获取一个可用的服务实例（基于负载的智能分配）"""
        async with self.lock:
            # 查找负载最低的服务实例
            min_requests = float('inf')
            best_service = None
            best_index = 0

            for i, service in enumerate(self.services):
                async with self.stats_lock:
                    stats = self.service_stats.get(i, {'active_requests': 0, 'last_used': 0})
                    active_requests = stats['active_requests']

                # 优先选择活跃请求数最少的服务
                if active_requests < min_requests:
                    min_requests = active_requests
                    best_service = service
                    best_index = i

            # 更新服务状态
            async with self.stats_lock:
                if best_index not in self.service_stats:
                    self.service_stats[best_index] = {'active_requests': 0, 'last_used': 0}
                self.service_stats[best_index]['active_requests'] += 1
                self.service_stats[best_index]['last_used'] = time.time()

            return best_service

    async def release_service(self, service: KiroApiService):
        """释放服务实例"""
        async with self.lock:
            # 查找服务索引
            for i, s in enumerate(self.services):
                if s == service:
                    async with self.stats_lock:
                        if i in self.service_stats:
                            self.service_stats[i]['active_requests'] = max(0, self.service_stats[i]['active_requests'] - 1)
                    break

    async def close_all(self):
        """关闭所有服务"""
        for service in self.services:
            await service.close()
        self.services.clear()


# 创建全局服务池实例
_service_pool: Optional[KiroServicePool] = None


def get_kiro_service() -> KiroServicePool:
    """获取 Kiro 服务池实例

    Returns:
        KiroServicePool: 服务池实例

    注意：首次调用时会创建服务池实例，但不会初始化服务
    需要显式调用 pool.initialize() 来初始化服务池中的服务实例
    """
    global _service_pool
    if _service_pool is None:
        from ..config import settings
        _service_pool = KiroServicePool(pool_size=settings.POOL_SIZE)
    return _service_pool


async def get_kiro_service_from_pool() -> KiroApiService:
    """从池中获取 Kiro 服务实例"""
    pool = get_kiro_service()
    if not pool.services:
        await pool.initialize()
    return await pool.get_service()
