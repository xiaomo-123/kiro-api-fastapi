import time
# 消息控制器
import logging
import asyncio
import uuid
from typing import Dict, Any, Optional
from fastapi import Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

from ..models import ClaudeMessageRequest, ErrorResponse
from ..services.kiro_service import get_kiro_service
from ..config import settings

logger = logging.getLogger(__name__)


class MessageController:
    """消息控制器"""

    def __init__(self):
        # 不在初始化时获取服务池，避免循环依赖
        # 请求去重：使用字典跟踪正在处理的请求
        self._processing_requests: Dict[str, asyncio.Event] = {}
        self._request_lock = asyncio.Lock()
        # 请求超时时间（秒）
        self._request_timeout = 600        
    async def _generate_request_id(self, body: ClaudeMessageRequest) -> str:
        """生成请求唯一标识"""
        # 使用UUID和微秒级时间戳确保唯一性
        import time
        import uuid

        # 使用UUID确保唯一性
        request_uuid = str(uuid.uuid4())

        # 使用微秒级时间戳
        timestamp = int(time.time() * 1000000)  # 微秒级时间戳

        # 使用模型和请求UUID生成唯一ID
        return f"{body.model}_{request_uuid}_{timestamp}"

    async def _check_duplicate_request(self, request_id: str) -> bool:
        """检查是否为重复请求"""
        async with self._request_lock:
            if request_id in self._processing_requests:
                # 检查请求是否超时（超过10分钟视为过期）
                request_data = self._processing_requests[request_id]
                request_time = request_data.get('timestamp', 0)
                current_time = time.time()

                # 如果请求超过10分钟未完成，视为过期
                if current_time - request_time > 600:
                    # 请求已过期，清理旧记录
                    self._processing_requests.pop(request_id, None)
                    logger.info(f'[MessageController] Cleaned up expired request: {request_id}')
                    return False

                # 请求仍在处理中，视为重复
                logger.warning(f'[MessageController] Duplicate request detected: {request_id}')
                return True
            return False

    async def _register_request(self, request_id: str):
        """注册请求"""
        import time
        async with self._request_lock:
            self._processing_requests[request_id] = {
                'event': asyncio.Event(),
                'timestamp': time.time()
            }

    async def _unregister_request(self, request_id: str):
        """注销请求"""
        async with self._request_lock:
            request_data = self._processing_requests.pop(request_id, None)
            if request_data:
                # 设置事件标志，表示请求已完成
                request_data['event'].set()

    async def handle_message(
        self,
        request: Optional[Request],
        body: ClaudeMessageRequest
    ) -> Any:
        """处理消息请求"""
        # 生成请求ID
        request_id = await self._generate_request_id(body)

        # 检查是否为重复请求
        if await self._check_duplicate_request(request_id):
            # 返回409状态码表示冲突（重复请求）
            raise HTTPException(
                status_code=409,
                detail={
                    'type': 'error',
                    'error': {
                        'type': 'duplicate_request',
                        'message': '检测到重复请求，请勿重复提交相同请求',
                        'request_id': request_id
                    }
                }
            )

        # 注册请求
        await self._register_request(request_id)

        # 从服务池获取服务实例
        from app.services.kiro_service_new import get_kiro_service_from_pool, get_kiro_service
        kiro_pool = get_kiro_service()
        kiro_service = await get_kiro_service_from_pool()

        try:
            # 确保服务已初始化（带超时保护）
            try:
                # 增加超时时间到60秒，给初始化更多时间
                init_result = await asyncio.wait_for(
                    kiro_service.initialize(),
                    timeout=60.0  # 60秒超时
                )

                # 如果initialize返回False，表示没有可用账号
                if init_result is False:
                    error_msg = '服务暂不可用：没有可用的Kiro账号。请通过管理界面添加账号后再试。'
                    logger.error(f'[MessageController] {error_msg}')

                    # 添加账号池状态信息
                    from app.services.account_pool_v2 import redis_client
                    available_count = 0
                    redis_connected = False
                    if redis_client:
                        try:
                            available_count = redis_client.zcard("available_accounts")
                            redis_connected = True
                            logger.error(f'[MessageController] Available accounts in pool: {available_count}')
                        except Exception as e:
                            logger.error(f'[MessageController] Failed to check account pool: {e}')
                    else:
                        logger.error('[MessageController] Redis client not available')

                    raise HTTPException(
                        status_code=503,
                        detail={
                            'type': 'error',
                            'error': {
                                'type': 'service_unavailable',
                                'message': error_msg,
                                'details': {
                                    'available_accounts': available_count,
                                    'redis_connected': redis_connected
                                }
                            }
                        }
                    )

            except asyncio.TimeoutError:
                error_msg = '服务初始化超时。请检查Redis连接和账号池状态。'
                logger.error(f'[MessageController] {error_msg}')
                raise HTTPException(
                    status_code=503,
                    detail={
                        'type': 'error',
                        'error': {
                            'type': 'service_unavailable',
                            'message': error_msg
                        }
                    }
                )
            except HTTPException:
                # HTTPException直接重新抛出
                raise
            except ValueError as e:
                # 处理没有可用账号的情况
                error_msg = str(e)
                if 'No active accounts' in error_msg or 'No refresh token' in error_msg:
                    raise HTTPException(
                        status_code=503,
                        detail={
                            'type': 'error',
                            'error': {
                                'type': 'service_unavailable',
                                'message': '服务暂时不可用：没有可用的Kiro账号。请通过管理界面添加账号后再试。'
                            }
                        }
                    )
                raise

            # 根据是否流式选择处理方式
            if body.stream:
                return StreamingResponse(
                    self._stream_response(body),
                    media_type='text/event-stream',
                    headers={
                        'Cache-Control': 'no-cache, no-transform',
                        'Connection': 'keep-alive',
                        'X-Accel-Buffering': 'no'
                    }
                )
            else:
                try:
                    response = await kiro_service.generate_content(
                        body.model,
                        body.dict(exclude_unset=True)
                    )

                    # 检查响应中是否包含错误
                    if isinstance(response, dict) and 'error' in response:
                        error_msg = response.get('error', 'Unknown error')
                        if isinstance(error_msg, dict):
                            error_msg = error_msg.get('message', str(error_msg))
                        
                        # 判断错误类型并返回适当的 HTTP 状态码
                        if error_msg == "403":
                            # 处理403错误：禁用账号、停止实例、切换账号
                            current_account_id = kiro_service.current_account_id
                            logger.error(f'[MessageController] 非流式 Received 403 error for account {current_account_id}, disabling account and switching')

                            # 1. 释放旧账号的并发计数
                            from app.services.account_concurrency_manager import release_account as release_concurrency
                            await release_concurrency(current_account_id, force=True)
                            logger.info(f'[MessageController] 非流式 Released concurrency for account {current_account_id}')

                            # 2. 禁用当前账号
                            disable_result = await kiro_service._disable_current_account()
                            if disable_result:
                                logger.info(f'[MessageController] 非流式 Successfully disabled account {current_account_id}')
                            else:
                                logger.error(f'[MessageController] Failed to disable account {current_account_id}')

                            # 3. 停止当前实例
                            await kiro_service.close()
                           

                            # 4. 切换到下一个账号
                            switch_result = await kiro_service._switch_to_next_account()
                            new_account_id = kiro_service.current_account_id
                            # if switch_result:
                            #     logger.info(f'[MessageController] Successfully switched from account {current_account_id} to account {new_account_id}')
                            # else:
                            #     logger.error(f'[MessageController] Failed to switch from account {current_account_id}')

                            # 5. 释放当前服务实例
                            await kiro_pool.release_service(kiro_service)

                            # 5. 获取新的服务实例
                            kiro_service = await get_kiro_service_from_pool()

                            # 6. 初始化新实例
                            init_result = await asyncio.wait_for(
                                kiro_service.initialize(),
                                timeout=60.0
                            )

                            if init_result is False:
                                # 没有可用账号，返回503状态码
                                logger.error(f'[MessageController] No available accounts after disabling account {current_account_id}')
                                raise HTTPException(status_code=503)

                            # 7. 使用新实例重新尝试请求
                            response = await kiro_service.generate_content(
                                body.model,
                                body.dict(exclude_unset=True)
                            )

                            # 8. 检查新实例的响应
                            if isinstance(response, dict) and 'error' in response:
                                error_msg = response.get('error', 'Unknown error')
                                if isinstance(error_msg, dict):
                                    error_msg = error_msg.get('message', str(error_msg))

                                # 如果新实例仍然返回403，说明所有账号都不可用，返回503状态码
                                if '403' in str(error_msg) or 'Forbidden' in str(error_msg):
                                    # 释放新账号的并发计数器
                                    new_account_id = kiro_service.current_account_id
                                    from app.services.account_concurrency_manager import release_account as release_concurrency
                                    await release_concurrency(new_account_id, force=True)
                                    logger.info(f'[MessageController] 非流式 new account {new_account_id} due to 403 error')
                                   
                                    # 直接返回503错误响应，不抛出异常
                                    return JSONResponse(
                                        status_code=503,
                                        content={
                                            'type': 'error',
                                            'error': {
                                                'type': 'service_unavailable',
                                                'message': '服务暂时不可用：所有账号都返回403错误，请添加新的有效账号后重试'
                                            }
                                        }
                                    )

                                # 其他错误类型返回500状态码
                                raise HTTPException(status_code=500)

                            # 9. 返回新实例的响应
                            return JSONResponse(content=response)
                        elif error_msg == "429":
                            # 处理429错误：停止实例、切换账号和实例（不禁用账号）
                            current_account_id = kiro_service.current_account_id
                            logger.error(f'[MessageController] 非流式  429 error for account {current_account_id}, switching account and instance (not disabling account)')

                            # 1. 停止当前实例
                            await kiro_service.close()
                            

                            # 2. 切换到下一个账号
                            switch_result = await kiro_service._switch_to_next_account()
                            new_account_id = kiro_service.current_account_id
                            #

                            # 3. 释放当前服务实例
                            await kiro_pool.release_service(kiro_service)

                            # 4. 获取新的服务实例
                            kiro_service = await get_kiro_service_from_pool()

                            # 5. 初始化新实例
                            init_result = await asyncio.wait_for(
                                kiro_service.initialize(),
                                timeout=60.0
                            )

                            if init_result is False:
                                # 没有可用账号，返回503状态码
                                logger.error(f'[MessageController] No available accounts after switching account {current_account_id}')
                                raise HTTPException(status_code=503)

                            # 6. 使用新实例重新尝试请求
                            response = await kiro_service.generate_content(
                                body.model,
                                body.dict(exclude_unset=True)
                            )

                            # 7. 检查新实例的响应
                            if isinstance(response, dict) and 'error' in response:
                                new_error_msg = response.get('error', 'Unknown error')
                                if isinstance(new_error_msg, dict):
                                    new_error_msg = new_error_msg.get('message', str(new_error_msg))

                                # 如果新实例仍然返回429，则直接返回429状态码
                                if new_error_msg == "429":
                                    # 释放新账号的并发计数器
                                    new_account_id = kiro_service.current_account_id
                                    from app.services.account_concurrency_manager import release_account as release_concurrency
                                    await release_concurrency(new_account_id, force=True)
                                    logger.info(f'[MessageController] Released concurrency for new account {new_account_id} due to 429 error')
                                    raise HTTPException(status_code=429)

                                # 其他错误类型返回500状态码
                                raise HTTPException(status_code=500)

                            # 8. 返回新实例的响应
                            return JSONResponse(content=response)
                        else:
                            raise HTTPException(
                                status_code=500,
                                detail={
                                    'type': 'error',
                                    'error': {
                                        'type': 'server_error',
                                        'message': error_msg
                                    }
                                }
                            )

                    return JSONResponse(content=response)
                except HTTPException:
                    raise
                except Exception as e:
                    error_msg = str(e)
                    # 判断错误类型并返回适当的 HTTP 状态码
                    if '429' in error_msg or 'Too many requests' in error_msg:
                        raise HTTPException(
                            status_code=429,
                            detail={
                                'type': 'error',
                                'error': {
                                    'type': 'rate_limit_exceeded',
                                    'message': '请求过于频繁，请稍后再试',
                                    'retry_after': 60
                                    }
                            }
                        )
                    elif 'Failed to read API response' in error_msg:
                        raise HTTPException(status_code=502)
                    else:
                        raise HTTPException(status_code=500)

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f'Error handling message: {e}', exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={
                    'type': 'error',
                    'error': {
                        'type': 'server_error',
                        'message': str(e)
                    }
                }
            )
        finally:
            # 释放服务实例
            await kiro_pool.release_service(kiro_service)
            # 注销请求
            await self._unregister_request(request_id)

    async def _stream_response(self, body: ClaudeMessageRequest):
        """流式响应生成器"""
        # 生成请求ID
        request_id = await self._generate_request_id(body)

        # 检查是否为重复请求
        if await self._check_duplicate_request(request_id):
            # 返回429状态码表示请求过于频繁
            error_event = {'status_code': 429}
            yield f"event: error\ndata: {self._to_json(error_event)}\n\n"
            return

        # 注册请求
        await self._register_request(request_id)

        # 从服务池获取服务实例
        from app.services.kiro_service_new import get_kiro_service_from_pool, get_kiro_service
        kiro_pool = get_kiro_service()
        kiro_service = await get_kiro_service_from_pool()

        try:
            # 添加超时控制
            stream = kiro_service.generate_content_stream(
                body.model,
                body.dict(exclude_unset=True)
            )

            async for event in stream:
                # 检查事件中是否包含错误
                if isinstance(event, dict) and 'error' in event:
                    error_msg = event.get('error', '')
                    # if isinstance(error_msg, dict):
                    #     error_msg = error_msg.get('message', str(error_msg))
                    
                    # 判断错误类型并发送适当的错误事件
                    if error_msg == "403" and 'account_id' in event:
                        # 处理403错误：禁用账号、停止实例、切换账号
                        current_account_id =event.get('account_id')
                        logger.error(f'[MessageController] Received 403 error in stream for account {current_account_id}, disabling account and switching')

                        # 1. 释放旧账号的并发计数
                        from app.services.account_concurrency_manager import release_account as release_concurrency
                        await release_concurrency(current_account_id, force=True)
                        logger.info(f'[MessageController] Released concurrency for account {current_account_id}')

                        # 2. 禁用当前账号
                        disable_result = await kiro_service._disable_current_account()
                        if disable_result:
                            logger.info(f'[MessageController] Successfully disabled account {current_account_id}')
                        else:
                            logger.error(f'[MessageController] Failed to disable account {current_account_id}')

                        # 3. 停止当前实例
                        await kiro_service.close()
                        logger.info(f'[MessageController] Closed service instance for account {current_account_id}')

                        # 4. 切换到下一个账号
                        switch_result = await kiro_service._switch_to_next_account()
                        new_account_id = kiro_service.current_account_id
                        if switch_result:
                            logger.info(f'[MessageController] Successfully switched from account {current_account_id} to account {new_account_id}')
                        else:
                            logger.error(f'[MessageController] Failed to switch from account {current_account_id}')

                        # 4. 释放当前服务实例
                        await kiro_pool.release_service(kiro_service)

                        # 5. 获取新的服务实例
                        kiro_service = await get_kiro_service_from_pool()

                        # 6. 初始化新实例
                        init_result = await asyncio.wait_for(
                            kiro_service.initialize(),
                            timeout=60.0
                        )

                        if init_result is False:
                            error_event = {
                                'type': 'error',
                                'error': {
                                    'type': 'service_unavailable',
                                    'message': '服务暂不可用：没有可用的Kiro账号。请通过管理界面添加账号后再试。'
                                }
                            }
                            yield f"event: error\ndata: {self._to_json(error_event)}\n\n"
                            return

                        # 7. 使用新实例重新尝试请求
                        try:
                            new_stream = kiro_service.generate_content_stream(
                                body.model,
                                body.dict(exclude_unset=True)
                            )

                            # 8. 转发新实例的流式响应
                            async for new_event in new_stream:
                                # 检查新实例是否返回错误
                                if isinstance(new_event, dict) and 'error' in new_event:
                                    new_error_msg = new_event.get('error', '')
                                    # if isinstance(new_error_msg, dict):
                                    #     new_error_msg = new_error_msg.get('message', str(new_error_msg))

                                    # 如果新实例仍然返回403，则返回错误
                                    if new_error_msg == "403" and 'account_id' in new_event:
                                        # 释放新账号的并发计数器
                                        new_account_id = kiro_service.current_account_id
                                        from app.services.account_concurrency_manager import release_account as release_concurrency
                                        await release_concurrency(new_account_id, force=True)
                                        logger.info(f'[MessageController] Released concurrency for new account {new_account_id} due to 403 error in stream')
                                        error_event = {
                                            'type': 'error',
                                            'error': {
                                                'type': 'forbidden',
                                                'message': '账号已被禁用，正在切换到其他账号'
                                            }
                                        }
                                        yield f"event: error\ndata: {self._to_json(error_event)}\n\n"
                                        return

                                    # 其他错误类型正常处理
                                    error_event = {
                                        'type': 'error',
                                        'error': {
                                            'type': 'server_error',
                                            'message': new_error_msg
                                        }
                                    }
                                    yield f"event: error\ndata: {self._to_json(error_event)}\n\n"
                                    return

                                # 格式化为 SSE 事件
                                event_type = new_event.get('type')
                                data = new_event

                                yield f"event: {event_type}\n"
                                yield f"data: {self._to_json(data)}\n\n"
                        except Exception as e:
                            logger.error(f'[MessageController] Error in new stream: {e}', exc_info=True)
                            error_event = {
                                'type': 'error',
                                'error': {
                                    'type': 'server_error',
                                    'message': '服务器内部错误，请稍后再试'
                                }
                            }
                            yield f"event: error\ndata: {self._to_json(error_event)}\n\n"

                        return
                    elif error_msg == "429" and 'account_id' in event:
                        # 处理429错误：停止实例、切换账号和实例（不禁用账号）
                        current_account_id = event.get('account_id')
                        logger.error(f'[MessageController] Received 429 error in stream for account {current_account_id}, switching account and instance (not disabling account)')

                        # 1. 停止当前实例
                        await kiro_service.close()
                        logger.info(f'[MessageController] Closed service instance for account {current_account_id}')

                        # 2. 切换到下一个账号
                        switch_result = await kiro_service._switch_to_next_account()
                        new_account_id = kiro_service.current_account_id
                        if switch_result:
                            logger.info(f'[MessageController] Successfully switched from account {current_account_id} to account {new_account_id}')
                        else:
                            logger.error(f'[MessageController] Failed to switch from account {current_account_id}')

                        # 3. 释放当前服务实例
                        await kiro_pool.release_service(kiro_service)

                        # 4. 获取新的服务实例
                        kiro_service = await get_kiro_service_from_pool()

                        # 5. 初始化新实例
                        init_result = await asyncio.wait_for(
                            kiro_service.initialize(),
                            timeout=60.0
                        )

                        if init_result is False:
                            # 没有可用账号，返回503状态码
                            logger.error(f'[MessageController] No available accounts after switching account {current_account_id}')
                            error_event = {
                                'type': 'error',
                                'error': {
                                    'type': 'service_unavailable',
                                    'message': '服务暂不可用：没有可用的Kiro账号。请通过管理界面添加账号后再试。'
                                }
                            }
                            yield f"event: error\ndata: {self._to_json(error_event)}\n\n"
                            return

                        # 6. 使用新实例重新尝试请求
                        try:
                            new_stream = kiro_service.generate_content_stream(
                                body.model,
                                body.dict(exclude_unset=True)
                            )

                            # 7. 转发新实例的流式响应
                            async for new_event in new_stream:
                                # 检查新实例是否返回错误
                                if isinstance(new_event, dict) and 'error' in new_event:
                                    new_error_msg = new_event.get('error', 'Unknown error')
                                    if isinstance(new_error_msg, dict):
                                        new_error_msg = new_error_msg.get('message', str(new_error_msg))

                                    # 如果新实例仍然返回429，则直接返回429状态码
                                    if new_error_msg == "429" and 'account_id' in new_event:
                                        # 释放新账号的并发计数器
                                        new_account_id = kiro_service.current_account_id
                                        from app.services.account_concurrency_manager import release_account as release_concurrency
                                        await release_concurrency(new_account_id, force=True)
                                        logger.info(f'[MessageController] Released concurrency for new account {new_account_id} due to 429 error in stream')
                                        error_event = {
                                            'type': 'error',
                                            'error': {
                                                'type': 'rate_limit_exceeded',
                                                'message': '请求过于频繁，请稍后再试',
                                                'retry_after': 60
                                            }
                                        }
                                        yield f"event: error\ndata: {self._to_json(error_event)}\n\n"
                                        return

                                    # 其他错误类型正常处理
                                    error_event = {
                                        'type': 'error',
                                        'error': {
                                            'type': 'server_error',
                                            'message': new_error_msg
                                        }
                                    }
                                    yield f"event: error\ndata: {self._to_json(error_event)}\n\n"
                                    return

                                # 格式化为 SSE 事件
                                event_type = new_event.get('type')
                                data = new_event

                                yield f"event: {event_type}\n"
                                yield f"data: {self._to_json(data)}\n\n"
                        except Exception as e:
                            logger.error(f'[MessageController] Error in new stream: {e}', exc_info=True)
                            error_event = {
                                'type': 'error',
                                'error': {
                                    'type': 'server_error',
                                    'message': '服务器内部错误，请稍后再试'
                                }
                            }
                            yield f"event: error\ndata: {self._to_json(error_event)}\n\n"

                        return
                    elif 'Failed to read API response' in str(error_msg):
                        error_event = {
                            'type': 'error',
                            'error': {
                                'type': 'bad_gateway',
                                'message': '上游服务响应异常，请稍后再试'
                            }
                        }
                    else:
                        error_event = {
                            'type': 'error',
                            'error': {
                                'type': 'server_error',
                                'message': error_msg
                            }
                        }
                    yield f"event: error\ndata: {self._to_json(error_event)}\n\n"
                    return
                
                # 格式化为 SSE 事件
                event_type = event.get('type')
                data = event
                yield f"event: {event_type}\n"
                yield f"data: {self._to_json(data)}\n\n"
                

        except asyncio.TimeoutError:
            logger.error(f'Stream response timeout after {settings.REQUEST_TIMEOUT} seconds')
            error_event = {
                'type': 'error',
                'error': {
                    'type': 'timeout',
                    'message': f'请求超时，请稍后再试（超时时间：{settings.REQUEST_TIMEOUT}秒）'
                }
            }
            yield f"event: error\ndata: {self._to_json(error_event)}\n\n"
        except Exception as e:
            logger.error(f'Error in stream: {e}', exc_info=True)
            error_msg = str(e)
            
            # 判断错误类型并发送适当的错误事件
            if '403' in error_msg or 'Forbidden' in error_msg:
                # 处理403错误：禁用账号、停止实例、切换账号
                current_account_id = kiro_service.current_account_id
                logger.error(f'[MessageController] Received 403 error in stream exception for account {current_account_id}, disabling account and switching')

                # 1. 禁用当前账号
                disable_result = await kiro_service._disable_current_account()
                if disable_result:
                    logger.info(f'[MessageController] Successfully disabled account {current_account_id}')
                else:
                    logger.error(f'[MessageController] Failed to disable account {current_account_id}')

                # 2. 停止当前实例
                await kiro_service.close()
                logger.info(f'[MessageController] Closed service instance for account {current_account_id}')

                # 3. 切换到下一个账号
                switch_result = await kiro_service._switch_to_next_account()
                new_account_id = kiro_service.current_account_id
                if switch_result:
                    logger.info(f'[MessageController] Successfully switched from account {current_account_id} to account {new_account_id}')
                else:
                    logger.error(f'[MessageController] Failed to switch from account {current_account_id}')

                # 4. 释放当前服务实例
                await kiro_pool.release_service(kiro_service)

                # 5. 获取新的服务实例
                kiro_service = await get_kiro_service_from_pool()

                # 6. 初始化新实例
                init_result = await asyncio.wait_for(
                    kiro_service.initialize(),
                    timeout=60.0
                )

                if init_result is False:
                    error_event = {
                        'type': 'error',
                        'error': {
                            'type': 'service_unavailable',
                            'message': '服务暂不可用：没有可用的Kiro账号。请通过管理界面添加账号后再试。'
                        }
                    }
                    yield f"event: error\ndata: {self._to_json(error_event)}\n\n"
                    return

                # 7. 使用新实例重新尝试请求
                try:
                    new_stream = kiro_service.generate_content_stream(
                        body.model,
                        body.dict(exclude_unset=True)
                    )

                    # 8. 转发新实例的流式响应
                    async for new_event in new_stream:
                        # 检查新实例是否返回错误
                        if isinstance(new_event, dict) and 'error' in new_event:
                            new_error_msg = new_event.get('error', 'Unknown error')
                            if isinstance(new_error_msg, dict):
                                new_error_msg = new_error_msg.get('message', str(new_error_msg))

                            # 如果新实例仍然返回403，则返回错误
                            if '403' in str(new_error_msg) or 'Forbidden' in str(new_error_msg):
                                # 释放新账号的并发计数器
                                new_account_id = kiro_service.current_account_id
                                from app.services.account_concurrency_manager import release_account as release_concurrency
                                await release_concurrency(new_account_id, force=True)
                                logger.info(f'[MessageController] Released concurrency for new account {new_account_id} due to 403 error in stream exception')
                                error_event = {
                                    'type': 'error',
                                    'error': {
                                        'type': 'forbidden',
                                        'message': '账号已被禁用，正在切换到其他账号'
                                    }
                                }
                                yield f"event: error\ndata: {self._to_json(error_event)}\n\n"
                                return

                            # 其他错误类型正常处理
                            error_event = {
                                'type': 'error',
                                'error': {
                                    'type': 'server_error',
                                    'message': new_error_msg
                                }
                            }
                            yield f"event: error\ndata: {self._to_json(error_event)}\n\n"
                            return

                        # 格式化为 SSE 事件
                        event_type = new_event.get('type')
                        data = new_event
                        yield f"event: {event_type}\n"
                        yield f"data: {self._to_json(data)}\n\n"
                        
                except Exception as e:
                    logger.error(f'[MessageController] Error in new stream: {e}', exc_info=True)
                    error_event = {
                        'type': 'error',
                        'error': {
                            'type': 'server_error',
                            'message': '服务器内部错误，请稍后再试'
                        }
                    }
                    yield f"event: error\ndata: {self._to_json(error_event)}\n\n"

                return
            elif '429' in error_msg or 'Too many requests' in error_msg:
                # 处理429错误：停止实例、切换账号和实例（不禁用账号）
                current_account_id = kiro_service.current_account_id
                logger.error(f'[MessageController] Received 429 error in stream exception for account {current_account_id}, switching account and instance (not disabling account)')

                # 1. 停止当前实例
                await kiro_service.close()
                logger.info(f'[MessageController] Closed service instance for account {current_account_id}')

                # 2. 切换到下一个账号
                switch_result = await kiro_service._switch_to_next_account()
                new_account_id = kiro_service.current_account_id
                if switch_result:
                    logger.info(f'[MessageController] Successfully switched from account {current_account_id} to account {new_account_id}')
                else:
                    logger.error(f'[MessageController] Failed to switch from account {current_account_id}')

                # 3. 释放当前服务实例
                await kiro_pool.release_service(kiro_service)

                # 4. 获取新的服务实例
                kiro_service = await get_kiro_service_from_pool()

                # 5. 初始化新实例
                init_result = await asyncio.wait_for(
                    kiro_service.initialize(),
                    timeout=60.0
                )

                if init_result is False:
                    error_event = {
                        'type': 'error',
                        'error': {
                            'type': 'service_unavailable',
                            'message': '服务暂不可用：没有可用的Kiro账号。请通过管理界面添加账号后再试。'
                        }
                    }
                    yield f"event: error\ndata: {self._to_json(error_event)}\n\n"
                    return

                # 6. 使用新实例重新尝试请求
                try:
                    new_stream = kiro_service.generate_content_stream(
                        body.model,
                        body.dict(exclude_unset=True)
                    )

                    # 7. 转发新实例的流式响应
                    async for new_event in new_stream:
                        # 检查新实例是否返回错误
                        if isinstance(new_event, dict) and 'error' in new_event:
                            new_error_msg = new_event.get('error', 'Unknown error')
                            if isinstance(new_error_msg, dict):
                                new_error_msg = new_error_msg.get('message', str(new_error_msg))

                            # 如果新实例仍然返回429，则返回错误
                            if '429' in str(new_error_msg) or 'Too many requests' in str(new_error_msg):
                                # 释放新账号的并发计数器
                                new_account_id = kiro_service.current_account_id
                                from app.services.account_concurrency_manager import release_account as release_concurrency
                                await release_concurrency(new_account_id, force=True)
                                logger.info(f'[MessageController] Released concurrency for new account {new_account_id} due to 429 error in stream exception')
                                error_event = {
                                    'type': 'error',
                                    'error': {
                                        'type': 'rate_limit_exceeded',
                                        'message': '请求过于频繁，请稍后再试',
                                        'retry_after': 60
                                    }
                                }
                                yield f"event: error\ndata: {self._to_json(error_event)}\n\n"
                                return

                            # 其他错误类型正常处理
                            error_event = {
                                'type': 'error',
                                'error': {
                                    'type': 'server_error',
                                    'message': new_error_msg
                                }
                            }
                            yield f"event: error\ndata: {self._to_json(error_event)}\n\n"
                            return

                        # 格式化为 SSE 事件
                        event_type = new_event.get('type')
                        data = new_event

                        yield f"event: {event_type}\n"
                        yield f"data: {self._to_json(data)}\n\n"
                except Exception as e:
                    logger.error(f'[MessageController] Error in new stream: {e}', exc_info=True)
                    error_event = {
                        'type': 'error',
                        'error': {
                            'type': 'server_error',
                            'message': '服务器内部错误，请稍后再试'
                        }
                    }
                    yield f"event: error\ndata: {self._to_json(error_event)}\n\n"

                return
            elif 'Failed to read API response' in error_msg:
                error_event = {
                    'type': 'error',
                    'error': {
                        'type': 'bad_gateway',
                        'message': '上游服务响应异常，请稍后再试'
                    }
                }
            else:
                error_event = {
                    'type': 'error',
                    'error': {
                        'type': 'server_error',
                        'message': '服务器内部错误，请稍后再试'
                    }
                }
            yield f"event: error\ndata: {self._to_json(error_event)}\n\n"
        finally:
            # 释放服务实例
            await kiro_pool.release_service(kiro_service)
            # 注销请求
            await self._unregister_request(request_id)

    def _to_json(self, obj: Any) -> str:
        """对象转 JSON 字符串"""
        import json
        return json.dumps(obj, ensure_ascii=False)


# 创建全局控制器实例
_message_controller: MessageController = None


def get_message_controller() -> MessageController:
    """获取消息控制器实例（单例）"""
    global _message_controller
    if _message_controller is None:
        _message_controller = MessageController()
    return _message_controller
