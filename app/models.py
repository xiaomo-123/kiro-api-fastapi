
# 数据模型
from typing import Optional, List, Any, Dict, Literal
from pydantic import BaseModel, Field, validator


class Message(BaseModel):
    """消息模型"""
    role: str
    content: Any
    tool_use_id: Optional[str] = None
    name: Optional[str] = None


class Tool(BaseModel):
    """工具定义模型"""
    name: str
    description: str
    input_schema: Dict[str, Any]


class ToolCall(BaseModel):
    """工具调用模型"""
    id: str
    type: Literal["function"] = "function"
    function: Dict[str, Any]


class ClaudeMessageRequest(BaseModel):
    """Claude消息请求模型"""
    model: str
    max_tokens: int = 4096
    messages: List[Message]
    system: Optional[Any] = None
    tools: Optional[List[Tool]] = None
    stream: bool = False
    temperature: Optional[float] = Field(None, ge=0.0, le=1.0)
    top_p: Optional[float] = Field(None, ge=0.0, le=1.0)
    top_k: Optional[int] = Field(None, ge=0)
    stop_sequences: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None

    class Config:
        # 允许额外字段（保持兼容性）
        extra = 'allow'

    @validator('max_tokens')
    def validate_max_tokens(cls, v):
        if v <= 0:
            raise ValueError('max_tokens must be positive')
        return v

    @validator('messages')
    def validate_messages(cls, v):
        if not v:
            raise ValueError('messages cannot be empty')
        return v


class ClaudeMessageResponse(BaseModel):
    """Claude消息响应模型"""
    id: str
    type: str = "message"
    role: str = "assistant"
    content: List[Any]
    model: str
    stop_reason: Optional[str] = None
    stop_sequence: Optional[str] = None
    usage: Dict[str, int]


class StreamEvent(BaseModel):
    """流式事件模型"""
    type: str
    index: Optional[int] = None
    delta: Optional[Dict[str, Any]] = None
    message: Optional[Dict[str, Any]] = None
    content_block: Optional[Dict[str, Any]] = None
    usage: Optional[Dict[str, int]] = None


class ErrorResponse(BaseModel):
    """错误响应模型"""
    type: str = "error"
    error: Dict[str, Any]
