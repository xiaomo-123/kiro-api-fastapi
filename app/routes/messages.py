
# 路由层
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ClaudeMessageRequest, ErrorResponse
from ..controllers.message_controller import get_message_controller
from ..config import settings

router = APIRouter()


async def verify_api_key(x_api_key: Optional[str] = Header(None, alias="X-Api-Key")):
   
    
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API Key不存在，请联系管理员")
    
    
    # 从 Redis 验证 API Key
    from ..services.apikey_manager import verify_api_key as verify_apikey_in_redis
    apikey_info = await verify_apikey_in_redis(x_api_key)
    if not apikey_info:
    
        raise HTTPException(status_code=404, detail="API Key不存在或已被禁用，请联系管理员")
    
    # 检查API Key状态

    
    return apikey_info


@router.post(
    '/claude-kiro-oauth/v1/messages',
    response_model=None
)
async def create_message(
    request: Request,
    body: ClaudeMessageRequest,
    api_key: dict = Depends(verify_api_key)
):
    """
    创建消息接口

    功能：
    - 接收 Claude 格式的消息请求
    - 转换为 Kiro API 格式
    - 调用 Kiro API
    - 返回 Claude 格式的响应

    优化：
    - 流式请求直接处理，保持实时性
    """
    # 获取消息控制器
    controller = get_message_controller()

    # 所有请求直接处理，不使用队列
    from fastapi.responses import StreamingResponse
    response = await controller.handle_message(request, body)
    if isinstance(response, StreamingResponse):
        # 确保流式响应有正确的超时配置
        response.headers['X-Accel-Buffering'] = 'no'  # 禁用nginx缓冲
        response.headers['Cache-Control'] = 'no-cache, no-transform'
    return response



