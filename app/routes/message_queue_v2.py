
# 高效请求队列管理器 V2
import asyncio
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
from dataclasses import dataclass, field
import uuid

logger = logging.getLogger(__name__)


@dataclass
class QueuedRequest:
    """队列中的请求"""
    request_id: str
    request_data: Dict[str, Any]
    future: asyncio.Future = field(compare=False)
    created_at: float = field(default_factory=lambda: asyncio.get_event_loop().time())


class MessageQueueV2:
    """高效消息队列管理器 - 0阻塞、协程级高效、多核友好"""

    def __init__(
        self,
        max_queue_size: int = 10000,
        max_workers: int = 100,
        request_timeout: float = 300.0
    ):
        """
        初始化消息队列管理器

        Args:
            max_queue_size: 最大队列大小
            max_workers: 最大并发工作协程数
            request_timeout: 请求超时时间（秒）
        """
        self.max_queue_size = max_queue_size
        self.max_workers = max_workers
        self.request_timeout = request_timeout

        # 使用有界队列防止内存溢出
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)

        # 工作协程列表
        self.workers: List[asyncio.Task] = []

        # 统计信息
        self.stats = {
            'total_requests': 0,
            'processed_requests': 0,
            'failed_requests': 0,
            'timeout_requests': 0,
            'active_workers': 0,
            'queue_size': 0
        }

        # 启动消费者协程
        self._start_workers()
        logger.info(f"MessageQueueV2 initialized with max_queue_size={max_queue_size}, max_workers={max_workers}")

    def _start_workers(self):
        """启动消费者协程"""
        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker(i))
            self.workers.append(worker)
        logger.info(f"Started {self.max_workers} worker coroutines")

    async def submit_request(
        self,
        request_data: Dict[str, Any]
    ) -> Any:
        """
        提交请求到队列（0阻塞，微秒级）

        Args:
            request_data: 请求数据

        Returns:
            请求处理结果
        """
        # 创建 Future 用于等待结果
        future = asyncio.Future()

        # 创建队列请求
        queued_request = QueuedRequest(
            request_id=str(uuid.uuid4()),
            request_data=request_data,
            future=future
        )

        try:
            # 使用 put_nowait 实现非阻塞入队
            self.queue.put_nowait(queued_request)
            self.stats['total_requests'] += 1
            self.stats['queue_size'] = self.queue.qsize()
        except asyncio.QueueFull:
            # 队列满时立即返回错误
            future.set_exception(Exception("Queue is full, please try again later"))
            self.stats['failed_requests'] += 1
            raise Exception("Queue is full, please try again later")

        # 等待结果（带超时）
        try:
            result = await asyncio.wait_for(future, timeout=self.request_timeout)
            self.stats['processed_requests'] += 1
            return result
        except asyncio.TimeoutError:
            self.stats['timeout_requests'] += 1
            logger.error(f"Request {queued_request.request_id} timeout after {self.request_timeout}s")
            raise Exception(f"Request timeout after {self.request_timeout}s")
        except Exception as e:
            self.stats['failed_requests'] += 1
            logger.error(f"Request {queued_request.request_id} failed: {e}")
            raise

    async def _worker(self, worker_id: int):
        """工作协程 - 从队列中获取请求并处理"""
        while True:
            try:
                # 从队列中获取请求（阻塞等待）
                queued_request = await self.queue.get()

                # 更新活跃工作协程数
                self.stats['active_workers'] += 1
                self.stats['queue_size'] = self.queue.qsize()

                try:
                    # 处理请求
                    result = await self._process_request(queued_request)

                    # 设置结果
                    if not queued_request.future.done():
                        queued_request.future.set_result(result)
                except Exception as e:
                    # 设置异常
                    if not queued_request.future.done():
                        queued_request.future.set_exception(e)
                finally:
                    # 更新活跃工作协程数
                    self.stats['active_workers'] -= 1

                    # 标记任务完成
                    self.queue.task_done()

            except asyncio.CancelledError:
                logger.info(f"Worker {worker_id} cancelled")
                break
            except Exception as e:
                logger.error(f"Worker {worker_id} error: {e}")
                await asyncio.sleep(0.1)  # 防止无限循环

    async def _process_request(self, queued_request: QueuedRequest):
        """处理单个请求"""
        try:
            # 从控制器获取处理函数
            from ..controllers.message_controller import get_message_controller
            controller = get_message_controller()

            # 获取请求体和请求对象
            request_data = queued_request.request_data
            request = request_data.get('request')
            body = request_data.get('body')

            # 处理请求
            result = await controller.handle_message(request, body)
            return result

        except Exception as e:
            logger.error(f"Error processing request {queued_request.request_id}: {e}")
            raise

    async def shutdown(self):
        """关闭队列管理器"""
        logger.info("Shutting down MessageQueueV2...")

        # 取消所有工作协程
        for worker in self.workers:
            worker.cancel()

        # 等待所有工作协程结束
        if self.workers:
            await asyncio.gather(*self.workers, return_exceptions=True)

        # 取消所有待处理的请求
        while not self.queue.empty():
            try:
                queued_request = self.queue.get_nowait()
                if not queued_request.future.done():
                    queued_request.future.set_exception(
                        Exception("Queue manager is shutting down")
                    )
            except asyncio.QueueEmpty:
                break

        logger.info("MessageQueueV2 shutdown complete")

    def get_stats(self) -> Dict[str, int]:
        """获取统计信息"""
        return self.stats.copy()


# 全局队列管理器实例
_queue_manager: Optional[MessageQueueV2] = None


def get_queue_manager() -> MessageQueueV2:
    """获取全局队列管理器实例"""
    global _queue_manager
    if _queue_manager is None:
        _queue_manager = MessageQueueV2(
            max_queue_size=10000,      # 最大队列大小
            max_workers=800,           # 进一步增加最大并发工作协程数
            request_timeout=120.0      # 减少请求超时时间（秒），与配置文件保持一致
        )
    return _queue_manager
