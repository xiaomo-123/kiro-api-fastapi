
# 消息控制器
import logging
from typing import Dict, Any
from fastapi import Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

from ..models import ClaudeMessageRequest, ErrorResponse
from ..services.kiro_service import get_kiro_service
from ..utils import log_conversation

logger = logging.getLogger(__name__)


class MessageController:
    """消息控制器"""

    def __init__(self):
        self.kiro_service = get_kiro_service()

    async def handle_message(
        self,
        request: Request,
        body: ClaudeMessageRequest
    ) -> Any:
        """处理消息请求"""
        try:
            # 确保服务已初始化
            await self.kiro_service.initialize()

            # 记录输入
            log_conversation(
                'input',
                str(body.messages),
                'none',
                'prompt_log.txt'
            )

            # 根据是否流式选择处理方式
            if body.stream:
                return StreamingResponse(
                    self._stream_response(body),
                    media_type='text/event-stream',
                    headers={
                        'Cache-Control': 'no-cache',
                        'Connection': 'keep-alive'
                    }
                )
            else:
                response = await self.kiro_service.generate_content(
                    body.model,
                    body.dict(exclude_unset=True)
                )

                # 记录输出
                log_conversation(
                    'output',
                    str(response),
                    'none',
                    'prompt_log.txt'
                )

                return JSONResponse(content=response)

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

    async def _stream_response(self, body: ClaudeMessageRequest):
        """流式响应生成器"""
        try:
            async for event in self.kiro_service.generate_content_stream(
                body.model,
                body.dict(exclude_unset=True)
            ):
                # 格式化为 SSE 事件
                event_type = event.get('type')
                data = event

                # Claude 流式响应需要 event 前缀
                if event_type in ['content_block_start', 'content_block_stop', 'message_stop']:
                    yield f"event: {event_type}\n"

                yield f"data: {self._to_json(data)}\n\n"

        except Exception as e:
            logger.error(f'Error in stream: {e}', exc_info=True)
            # 发送错误事件
            error_event = {
                'type': 'error',
                'error': {
                    'type': 'server_error',
                    'message': str(e)
                }
            }
            yield f"event: error\ndata: {self._to_json(error_event)}\n\n"

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
