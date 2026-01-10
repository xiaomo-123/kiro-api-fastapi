# 会话路由层（高并发版本）
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from typing import Optional
import asyncio
import json
from ..models import ClaudeMessageRequest
from ..config import settings
from ..services.account_pool_v2 import release_account
from ..services.proxy_pool import release_proxy

router = APIRouter()


async def verify_api_key(x_api_key: Optional[str] = Header(None, alias="X-Api-Key")):
    """验证API Key"""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API Key不存在，请联系管理员")

    from ..services.apikey_manager import verify_api_key as verify_apikey_in_redis
    apikey_info = await verify_apikey_in_redis(x_api_key)
    if not apikey_info:
        raise HTTPException(status_code=404, detail="API Key不存在或已被禁用，请联系管理员")

    return apikey_info


@router.post(
    '/claude-kiro-oauth/v1/mmessages',
    response_model=None,
    tags=['会话消息']
)
async def create_message(
    request: Request,
    body: ClaudeMessageRequest,
    api_key: dict = Depends(verify_api_key)
):
    """
    创建消息接口（会话模式，高并发）

    功能：
    - 接收 Claude 格式的消息请求
    - 转换为 Kiro API 格式
    - 调用 Kiro API（会话模式）
    - 返回 Claude 格式的响应（支持流式和非流式）

    会话特性：
    - 支持多轮对话，保持上下文
    - 使用 session_id 标识会话
    - 自动管理会话生命周期
    - 支持高并发（200 QPS）
    """
    from ..services.kiro_session_service import KiroSessionService
    from ..services.kiro_session_manager import get_session_manager

    # 获取会话管理器
    session_manager = get_session_manager()

    # 获取或创建会话（从请求头）
    session_id = request.headers.get('X-Session-Id')

    session = await session_manager.get_or_create_session(
        session_id=session_id,
        account_id=0
    )

    # 更新会话ID（如果是新会话）
    if not session_id:
        session_id = session.session_id

    # 创建服务实例
    service = KiroSessionService()

    try:
        # 加载账号和代理
        account = await service._load_account_from_pool()
        if not account:
            raise HTTPException(
                status_code=503,
                detail={
                    'type': 'error',
                    'error': {
                        'type': 'service_unavailable',
                        'message': '服务暂不可用：没有可用的Kiro账号'
                    }
                }
            )

        service._load_creds_from_dict(account)
        service._generate_machine_id()

        # 加载代理
        service.proxy = await service._load_proxy_from_pool()

        # 确保token有效
        await service._ensure_token()

        # 设置会话ID
        service.set_conversation_id(session.conversation_id)

        # 获取会话历史
        history = await session.get_history()
        service.session_history = history

        # 处理请求
        if body.stream:
            # 流式响应
            from fastapi.responses import StreamingResponse

            async def stream_generator():
                try:
                    async for chunk in service._call_api_stream(
                        body.model,
                        body.dict(exclude_unset=True),
                        session.conversation_id,
                        history
                    ):
                        if isinstance(chunk, dict):
                            if 'error' in chunk:
                                error_msg = chunk.get('error', '')
                                if '403' in str(error_msg):
                                    # 处理403错误
                                    await release_account(service.current_account_id, success=False)
                                    error_event = {
                                        'type': 'error',
                                        'error': {
                                            'type': 'forbidden',
                                            'message': '账号已被禁用'
                                        }
                                    }
                                    yield f"event: error\ndata: {json.dumps(error_event, ensure_ascii=False)}\n\n"
                                    return
                                elif '429' in str(error_msg):
                                    # 处理429错误
                                    await release_account(service.current_account_id, success=False)
                                    error_event = {
                                        'type': 'error',
                                        'error': {
                                            'type': 'rate_limit',
                                            'message': '请求过于频繁'
                                        }
                                    }
                                    yield f"event: error\ndata: {json.dumps(error_event, ensure_ascii=False)}\n\n"
                                    return

                            # 正常响应
                            event_type = chunk.get('type', 'message_delta')
                            yield f"event: {event_type}\ndata: {json.dumps(chunk, ensure_ascii=False)}\n\n"

                    yield "data: [DONE]\n\n"

                    # 添加响应到会话历史
                    # TODO: 从响应中提取内容并添加到历史

                except Exception as e:
                    import logging
                    logging.error(f'[MessagesSessions] Stream error: {e}', exc_info=True)
                    error_event = {
                        'type': 'error',
                        'error': {
                            'type': 'server_error',
                            'message': '服务器内部错误'
                        }
                    }
                    yield f"event: error\ndata: {json.dumps(error_event, ensure_ascii=False)}\n\n"
                finally:
                    # 释放资源
                    if service.current_account_id:
                        await release_account(service.current_account_id, success=True)
                    if service.current_proxy_id:
                        await release_proxy(service.current_proxy_id, success=True)

            return StreamingResponse(
                stream_generator(),
                media_type='text/event-stream',
                headers={
                    'Cache-Control': 'no-cache',
                    'Connection': 'keep-alive',
                    'X-Session-Id': session_id
                }
            )
        else:
            # 非流式响应
            from fastapi.responses import JSONResponse

            try:
                response = await service._call_api_nonstream(
                    body.model,
                    body.dict(exclude_unset=True),
                    session.conversation_id,
                    history
                )

                if isinstance(response, dict) and 'error' in response:
                    error_msg = response.get('error', '')
                    if '403' in str(error_msg):
                        await release_account(service.current_account_id, success=False)
                        raise HTTPException(
                            status_code=403,
                            detail={
                                'type': 'error',
                                'error': {
                                    'type': 'forbidden',
                                    'message': '账号已被禁用'
                                }
                            }
                        )
                    elif '429' in str(error_msg):
                        await release_account(service.current_account_id, success=False)
                        raise HTTPException(
                            status_code=429,
                            detail={
                                'type': 'error',
                                'error': {
                                    'type': 'rate_limit',
                                    'message': '请求过于频繁'
                                }
                            }
                        )

                # 添加响应到会话历史
                # TODO: 从响应中提取内容并添加到历史

                return JSONResponse(
                    content=response,
                    headers={'X-Session-Id': session_id}
                )
            except HTTPException:
                raise
            except Exception as e:
                import logging
                logging.error(f'[MessagesSessions] Non-stream error: {e}', exc_info=True)
                raise HTTPException(
                    status_code=500,
                    detail={
                        'type': 'error',
                        'error': {
                            'type': 'server_error',
                            'message': '服务器内部错误'
                        }
                    }
                )
            finally:
                # 释放资源
                if service.current_account_id:
                    await release_account(service.current_account_id, success=True)
                if service.current_proxy_id:
                    await release_proxy(service.current_proxy_id, success=True)

    except Exception as e:
        import logging
        logging.error(f'[MessagesSessions] Request error: {e}', exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                'type': 'error',
                'error': {
                    'type': 'server_error',
                    'message': '服务器内部错误'
                }
            }
        )


@router.get('/queue/health')
async def get_queue_health():
    """获取队列健康状态"""
    from ..services.kiro_session_manager import get_session_manager
    session_manager = get_session_manager()
    stats = await session_manager.get_session_stats()
    return {
        'status': 'ok',
        'session_stats': stats
    }


@router.post('/sessions/{session_id}/close')
async def close_session(
    session_id: str,
    api_key: dict = Depends(verify_api_key)
):
    """关闭指定会话"""
    from ..services.kiro_session_manager import get_session_manager
    session_manager = get_session_manager()

    success = await session_manager.close_session(session_id)
    if success:
        return {
            'status': 'success',
            'message': f'会话 {session_id} 已关闭'
        }
    else:
        raise HTTPException(
            status_code=404,
            detail={
                'type': 'error',
                'error': {
                    'type': 'session_not_found',
                    'message': f'会话 {session_id} 不存在'
                }
            }
        )


@router.get('/sessions/{session_id}')
async def get_session_info(
    session_id: str,
    api_key: dict = Depends(verify_api_key)
):
    """获取会话信息"""
    from ..services.kiro_session_manager import get_session_manager
    session_manager = get_session_manager()

    session_info = await session_manager.get_session_info(session_id)
    if session_info:
        return {
            'status': 'ok',
            'session': session_info
        }
    else:
        raise HTTPException(
            status_code=404,
            detail={
                'type': 'error',
                'error': {
                    'type': 'session_not_found',
                    'message': f'会话 {session_id} 不存在'
                }
            }
        )
