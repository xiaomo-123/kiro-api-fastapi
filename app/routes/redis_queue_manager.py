# Redis队列管理器 - 高并发优化版
import asyncio
import logging
import json
import uuid
import time
from typing import Dict, Any, Optional

import redis.asyncio as redis

from ..config import settings

logger = logging.getLogger(__name__)

# Redis连接池和客户端
redis_pool = None
redis_client = None


async def init_redis():
    """初始化Redis连接池和客户端"""
    global redis_pool, redis_client
    if redis_pool is None:
        try:
            redis_pool = redis.ConnectionPool(
                host=getattr(settings, "REDIS_HOST", "localhost"),
                port=getattr(settings, "REDIS_PORT", 6379),
                db=getattr(settings, "REDIS_DB", 0),
                password=getattr(settings, "REDIS_PASSWORD", None),
                max_connections=getattr(settings, "REDIS_MAX_CONNECTIONS", 2000),  # 限制最大连接数
                decode_responses=True,
                socket_timeout=3,
                socket_connect_timeout=3,
                retry_on_timeout=False
            )
            # 创建共享的Redis客户端
            redis_client = redis.Redis(connection_pool=redis_pool)
            await redis_client.ping()
            # logger.info("Redis queue manager connected successfully")
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}")
            redis_pool = None
            redis_client = None


async def get_redis_client():
    """获取共享的Redis客户端"""
    global redis_client
    if redis_client is None:
        await init_redis()
    return redis_client


class RedisQueueManager:
    """基于Redis的队列管理器 - 高并发、持久化、分布式"""

    def __init__(self, max_workers: int = None, request_timeout: float = None):
        """初始化队列管理器

        Args:
            max_workers: Worker数量，默认从配置读取
            request_timeout: 请求超时时间（秒），默认从配置读取
        """
        from ..config import settings

        # 使用配置的默认值
        self.max_workers = max_workers or settings.QUEUE_MANAGER_MAX_WORKERS
        self.request_timeout = request_timeout or settings.QUEUE_MANAGER_TIMEOUT

        self.workers = []
        self._workers_started = False
        self._redis_initialized = False

        # 使用配置的队列容量
        queue_size = settings.QUEUE_MANAGER_QUEUE_SIZE
        self._request_queue = asyncio.Queue(maxsize=queue_size)

        # 添加并发控制
        self._active_requests = 0
        self._max_concurrent_requests = self.max_workers  # 最大并发请求数

        self._pending_requests: Dict[str, asyncio.Event] = {}  # 待处理的请求
        self._request_results: Dict[str, Any] = {}  # 请求结果缓存
        # logger.info(f"RedisQueueManager initialized with {self.max_workers} workers, "
        #            f"timeout={self.request_timeout}s, queue_size={queue_size}")

    async def _ensure_redis_initialized(self):
        """确保Redis已初始化"""
        if not self._redis_initialized:
            await init_redis()
            self._redis_initialized = True

    async def _ensure_workers_started(self):
        """确保Worker协程已启动"""
        if not self._workers_started:
            self._start_workers()
            self._workers_started = True

    def _start_workers(self):
        """启动工作协程"""
        for i in range(self.max_workers):
            self.workers.append(asyncio.create_task(self._worker(i)))
        # logger.info(f"Started {self.max_workers} worker coroutines")

    async def _worker(self, worker_id: int):
        """工作协程 - 从内存队列中获取请求并处理"""
        # 获取共享的Redis客户端
        client = await get_redis_client()

        # 记录Worker启动
        logger.debug(f"Worker {worker_id} started")

        while True:
            try:
                # 检查并发限制
                if self._active_requests >= self._max_concurrent_requests:
                    logger.debug(f"Worker {worker_id} waiting, active requests: {self._active_requests}")
                    await asyncio.sleep(0.1)
                    continue

                # 从内存队列获取请求
                request_data = await self._request_queue.get()
                request_id = request_data.get('id')

                # 增加活跃请求计数
                self._active_requests += 1
                logger.debug(f"Worker {worker_id} processing request {request_id}, active: {self._active_requests}")

                # 处理请求
                try:
                    result_data = await self._process_request(request_data)
                    logger.debug(f"Worker {worker_id} completed request {request_id}")

                    # 处理响应对象
                    from starlette.responses import JSONResponse, StreamingResponse
                    if isinstance(result_data, JSONResponse):
                        # 提取JSONResponse响应体
                        result_data = json.loads(result_data.body.decode())
                    elif isinstance(result_data, StreamingResponse):
                        # StreamingResponse对象不能被序列化，需要特殊处理
                        # 将StreamingResponse转换为可序列化的标记
                        result_data = {
                            '_type': 'StreamingResponse',
                            'media_type': result_data.media_type,
                            'headers': dict(result_data.headers),
                            # 注意：StreamingResponse的body是异步生成器，无法序列化
                            # 这里只存储元数据，实际流式响应需要在返回时重新创建
                        }

                    # 将结果写入Redis（用于持久化）
                    await client.setex(
                        f"result:{request_data['id']}",
                        int(self.request_timeout),
                        json.dumps({'success': True, 'data': result_data})
                    )

                    # 将结果写入内存缓存
                    self._request_results[request_data['id']] = {
                        'success': True,
                        'data': result_data
                    }

                    # 通知请求已完成
                    event = self._pending_requests.get(request_data['id'])
                    if event:
                        event.set()

                except Exception as e:
                    # 处理失败，写入错误结果
                    import traceback
                    from fastapi import HTTPException

                    # 提取状态码
                    status_code = 500  # 默认500错误
                    if isinstance(e, HTTPException):
                        status_code = e.status_code

                    logger.error(f"Worker {worker_id} error processing request with status code: {status_code}")
                    logger.error(f"Worker {worker_id} error traceback: {traceback.format_exc()}")

                    await client.setex(
                        f"result:{request_data['id']}",
                        int(self.request_timeout),
                        json.dumps({'success': False, 'status_code': status_code})
                    )

                    # 将错误结果写入内存缓存
                    self._request_results[request_data['id']] = {
                        'success': False,
                        'status_code': status_code
                    }

                    # 通知请求已完成（即使失败）
                    event = self._pending_requests.get(request_data['id'])
                    if event:
                        event.set()

                finally:
                    # 减少活跃请求计数
                    self._active_requests -= 1
                    logger.debug(f"Worker {worker_id} finished request {request_id}, active: {self._active_requests}")

            except Exception as e:
                logger.error(f"Worker {worker_id} unexpected error: {e}")
                await asyncio.sleep(0.1)

    async def _process_request(self, request_data: Dict[str, Any]) -> Any:
        """处理单个请求

        Args:
            request_data: 请求数据，包含id和body

        Returns:
            处理结果

        Raises:
            Exception: 处理失败时抛出异常
        """
        from ..controllers.message_controller import get_message_controller
        from ..models import ClaudeMessageRequest

        controller = get_message_controller()

        # 从序列化的数据中重建请求对象
        body_dict = request_data.get('body', {})
        request_id = request_data.get('id', 'unknown')

        logger.debug(f"Reconstructing request {request_id} from body_dict")

        try:
            body = ClaudeMessageRequest(**body_dict)
            logger.debug(f"Successfully reconstructed ClaudeMessageRequest {request_id}, stream={body.stream}")
        except Exception as e:
            logger.error(f"Failed to reconstruct ClaudeMessageRequest {request_id}: {e}")
            logger.error(f"body_dict content: {body_dict}")
            raise

        # 处理请求（不需要Request对象，因为我们在队列中处理）
        # 使用超时保护，确保单个请求不会无限期阻塞
        try:
            result = await asyncio.wait_for(
                controller.handle_message(None, body),
                timeout=self.request_timeout  # 使用配置的超时时间
            )
            logger.debug(f"Request {request_id} processed successfully, result type: {type(result)}")
            return result
        except asyncio.TimeoutError:
            error_msg = f"Request {request_id} processing timeout after {self.request_timeout}s"
            logger.error(error_msg)
            from fastapi import HTTPException
            raise HTTPException(status_code=504)

    async def submit_request(self, request_data: Dict[str, Any], retry_count: int = 0) -> Any:
        """
        提交请求到内存队列

        Args:
            request_data: 请求数据（包含body）
            retry_count: 重试次数

        Returns:
            请求处理结果
        """
        # 检查重试次数限制
        MAX_RETRIES = 3
        if retry_count >= MAX_RETRIES:
            logger.error(f"Request reached maximum retry limit ({MAX_RETRIES}), giving up")
            from fastapi import HTTPException
            raise HTTPException(status_code=503)
        
        # 确保Redis已初始化
        await self._ensure_redis_initialized()

        # 确保Worker协程已启动
        await self._ensure_workers_started()

        # 获取Redis客户端
        client = await get_redis_client()

        # 生成请求ID
        request_id = str(uuid.uuid4())

        # 创建Event对象
        event = asyncio.Event()
        self._pending_requests[request_id] = event

        # 提取并序列化必要的信息（不序列化Request对象）
        body = request_data['body']
        logger.debug(f"Preparing request {request_id}, body type: {type(body)}, has dict: {hasattr(body, 'dict')}")

        serializable_data = {
            'id': request_id,
            'created_at': time.time(),
            'body': body.dict() if hasattr(body, 'dict') else body
        }

        logger.debug(f"Serialized request {request_id}, body type in serialized data: {type(serializable_data['body'])}")

        # 将请求推入内存队列
        await self._request_queue.put(serializable_data)
        logger.debug(f"Submitted request {request_id} to queue")

        # 等待结果
        try:
            # 使用两倍超时时间，确保Kiro服务有足够时间处理
            # Kiro服务超时: 600秒 (PROXY_TOTAL_TIMEOUT)
            # 队列管理器超时: 1200秒 (QUEUE_MANAGER_TIMEOUT)
            # 客户端等待超时: 2400秒 (2 * QUEUE_MANAGER_TIMEOUT)
            await asyncio.wait_for(event.wait(), timeout=self.request_timeout * 2)  # 两倍超时时间

            # 从结果缓存中获取结果
            result = self._request_results.get(request_id)

            if result is None:
                raise Exception(f"Request {request_id} result not found")

            # 检查是否成功
            if not result.get('success', True):
                status_code = result.get('status_code', 500)
                logger.error(f"Request {request_id} failed with status code: {status_code}")
                logger.error(f"Request {request_id} full result: {result}")
                
                # 对于403和429错误，自动重试
                if status_code in [403, 429]:
                    logger.info(f"Request {request_id} will be retried (attempt {retry_count + 1}/{MAX_RETRIES}) due to status code {status_code}")
                    # 添加重试延迟，避免立即重试
                    await asyncio.sleep(min(2 ** retry_count, 10))  # 指数退避，最大10秒
                    # 重新提交请求到队列，增加重试计数
                    return await self.submit_request(request_data, retry_count + 1)
                # 对于409错误（重复请求），不重试
                elif status_code == 409:
                    logger.warning(f"Request {request_id} is a duplicate request, not retrying")
                    raise HTTPException(status_code=409)
                
                # 其他错误直接抛出
                from fastapi import HTTPException
                raise HTTPException(status_code=status_code)

            logger.debug(f"Request {request_id} completed successfully")
            data = result.get('data')

            # 检查是否是StreamingResponse标记
            if isinstance(data, dict) and data.get('_type') == 'StreamingResponse':
                # 由于StreamingResponse的body是异步生成器，无法通过队列传递
                # 这里返回501状态码，表示不支持的功能
                from fastapi import HTTPException
                raise HTTPException(status_code=501)

            return data

        except asyncio.TimeoutError:
            logger.error(f"Request {request_id} timeout after {self.request_timeout}s")
            from fastapi import HTTPException
            raise HTTPException(status_code=504)

        finally:
            # 清理资源
            self._pending_requests.pop(request_id, None)
            self._request_results.pop(request_id, None)

    async def get_queue_health(self) -> Dict[str, Any]:
        """获取队列健康状态

        Returns:
            包含队列健康状态的字典
        """
        return {
            'queue_size': self._request_queue.qsize(),
            'active_requests': self._active_requests,
            'max_workers': self.max_workers,
            'max_concurrent': self._max_concurrent_requests,
            'pending_requests': len(self._pending_requests),
            'cached_results': len(self._request_results),
            'timeout': self.request_timeout
        }

    async def shutdown(self):
        """关闭队列管理器"""
        logger.info("Shutting down RedisQueueManager...")

        # 取消所有工作协程
        for worker in self.workers:
            worker.cancel()

        # 等待所有工作协程结束
        if self.workers:
            await asyncio.gather(*self.workers, return_exceptions=True)

        # 关闭Redis连接池和客户端
        global redis_pool, redis_client
        if redis_client is not None:
            await redis_client.close()
            redis_client = None
        if redis_pool is not None:
            await redis_pool.disconnect()
            redis_pool = None

        # logger.info("RedisQueueManager shutdown complete")


# 全局队列管理器实例
_queue_manager: Optional[RedisQueueManager] = None


def get_queue_manager() -> RedisQueueManager:
    """获取全局队列管理器实例"""
    global _queue_manager
    if _queue_manager is None:
        _queue_manager = RedisQueueManager(
            max_workers=1000,
            request_timeout=1200.0
        )
    return _queue_manager
