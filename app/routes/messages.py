
# 路由层
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from typing import Optional

from ..models import ClaudeMessageRequest, ErrorResponse
from ..controllers.message_controller import get_message_controller
from ..config import settings
from ..db.database import get_db
from ..db.models import ApiKey
from .message_queue_v2 import get_queue_manager

router = APIRouter()


@router.post(
    '/claude-kiro-oauth/v1/messages',
    response_model=None
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
    # 获取队列管理器
    queue_manager = get_queue_manager()

    # 提交请求到队列
    request_data = {
        'request': request,
        'body': body
    }

    # 通过队列管理器处理请求
    return await queue_manager.submit_request(request_data)
