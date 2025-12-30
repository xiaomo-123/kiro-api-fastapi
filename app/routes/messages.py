
# 路由层
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from typing import Optional

from ..models import ClaudeMessageRequest, ErrorResponse
from ..controllers.message_controller import get_message_controller
from ..config import settings
from ..db.database import get_db
from ..db.models import ApiKey

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
    if api_key:
        # 检查API Key表中是否存在状态为1的匹配项
        db = next(get_db())
        try:
            valid_api_key = db.query(ApiKey).filter(
                ApiKey.api_key == api_key,
                ApiKey.status == '1'
            ).first()
            
            if valid_api_key:
                return  # API Key在表中且状态为1，验证通过
        finally:
            db.close()
    
    # 如果API Key不在表中或状态不为1，则验证settings.REQUIRED_API_KEY
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
