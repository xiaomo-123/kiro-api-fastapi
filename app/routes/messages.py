
# 路由层
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from typing import Optional

from ..models import ClaudeMessageRequest, ErrorResponse
from ..controllers.message_controller import get_message_controller
from ..config import settings

router = APIRouter()


async def verify_authorization(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
) -> None:
    """验证授权"""
    api_key = None

    # 检查 Bearer token
    if authorization and authorization.startswith('Bearer '):
        api_key = authorization[7:]
    # 检查 x-api-key 头
    elif x_api_key:
        api_key = x_api_key

    # 验证 API key
    if api_key != settings.REQUIRED_API_KEY:
        raise HTTPException(
            status_code=401,
            detail={
                'type': 'error',
                'error': {
                    'type': 'authentication_error',
                    'message': 'Unauthorized: API key is invalid or missing.'
                }
            }
        )


@router.post(
    '/claude-kiro-oauth/v1/messages',
    response_model=None,
    dependencies=[Depends(verify_authorization)]
)
async def create_message(
    request: Request,
    body: ClaudeMessageRequest
):
    """
    创建消息接口

    功能：
    - 接收 Claude 格式的消息请求
    - 转换为 Kiro API 格式
    - 调用 Kiro API
    - 返回 Claude 格式的响应（支持流式）
    """
    controller = get_message_controller()
    return await controller.handle_message(request, body)
