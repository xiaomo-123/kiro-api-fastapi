
# 请求队列管理器
import asyncio
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class QueuedRequest:
    """队列中的请求"""
    request_id: str
    request_data: Dict[str, Any]
    future: asyncio.Future = field(compare=False)


class RequestQueueManager:
    """请求队列管理器 - FIFO"""

    def __init__(
        self,
        batch_size: int = 5,
        batch_timeout: float = 0.005
    ):
        """
        初始化请求队列管理器

        Args:
            batch_size: 批处理大小（减少以提高响应速度）
            batch_timeout: 批处理超时时间（秒）（减少以提高响应速度）
        """
        self.batch_size = batch_size
        self.batch_timeout = batch_timeout

        # FIFO 队列
        self.queue: asyncio.Queue = asyncio.Queue()

        # 统计信息
        self.stats = {
            'total_requests': 0,
            'processed_requests': 0,
            'failed_requests': 0,
            'batched_requests': 0
        }

        # 启动批处理任务
        self._batch_task = asyncio.create_task(self._process_batches())

        logger.info(f"RequestQueueManager initialized with batch_size={batch_size}")

    async def submit_request(
        self,
        request_data: Dict[str, Any]
    ) -> Any:
        """
        提交请求到队列

        Args:
            request_data: 请求数据

        Returns:
            请求处理结果
        """
        # 创建 Future 用于等待结果
        future = asyncio.Future()

        # 创建队列请求
        queued_request = QueuedRequest(
            request_id=str(hash(str(request_data))),
            request_data=request_data,
            future=future
        )

        # 添加到队列
        await self.queue.put(queued_request)
        self.stats['total_requests'] += 1

        # 等待结果
        try:
            result = await future
            self.stats['processed_requests'] += 1
            return result
        except Exception as e:
            self.stats['failed_requests'] += 1
            logger.error(f"Request failed: {e}")
            raise

    async def _process_batches(self):
        """处理批处理任务"""
        while True:
            try:
                # 收集一批请求
                batch = await self._collect_batch()

                if batch:
                    self.stats['batched_requests'] += len(batch)
                    # 并发处理这一批请求
                    await self._process_batch_concurrently(batch)

            except Exception as e:
                logger.error(f"Error processing batch: {e}")
                await asyncio.sleep(0.1)

    async def _collect_batch(self) -> List[QueuedRequest]:
        """收集一批请求"""
        batch = []
        deadline = asyncio.get_event_loop().time() + self.batch_timeout

        while len(batch) < self.batch_size:
            try:
                # 使用 wait_for 实现超时
                try:
                    queued_request = await asyncio.wait_for(
                        self.queue.get(),
                        timeout=deadline - asyncio.get_event_loop().time()
                    )
                    batch.append(queued_request)
                except asyncio.TimeoutError:
                    break
            except Exception as e:
                logger.error(f"Error collecting batch: {e}")
                break

        return batch

    async def _process_batch_concurrently(self, batch: List[QueuedRequest]):
        """并发处理一批请求"""
        # 创建任务列表
        tasks = []
        for queued_request in batch:
            task = asyncio.create_task(
                self._process_single_request(queued_request)
            )
            tasks.append(task)

        # 使用 asyncio.gather 并发执行
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 处理结果
        for result, queued_request in zip(results, batch):
            if isinstance(result, Exception):
                if not queued_request.future.done():
                    queued_request.future.set_exception(result)
            else:
                if not queued_request.future.done():
                    queued_request.future.set_result(result)

    async def _process_single_request(self, queued_request: QueuedRequest):
        """处理单个请求"""
        try:
            # 这里调用实际的请求处理逻辑
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
        if self._batch_task:
            self._batch_task.cancel()
            try:
                await self._batch_task
            except asyncio.CancelledError:
                pass

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

        logger.info("RequestQueueManager shutdown complete")

    def get_stats(self) -> Dict[str, int]:
        """获取统计信息"""
        return self.stats.copy()


# 全局队列管理器实例
_queue_manager: Optional[RequestQueueManager] = None


def get_queue_manager() -> RequestQueueManager:
    """获取全局队列管理器实例"""
    global _queue_manager
    if _queue_manager is None:
        _queue_manager = RequestQueueManager(
            batch_size=50,      # 批处理大小(针对高并发场景增加)
            batch_timeout=0.005  # 批处理超时(减少以加快请求处理速度)
        )
    return _queue_manager
