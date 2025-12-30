
# Kiro API 服务层
import json
import logging
import asyncio
import aiohttp
import os
import re
from typing import AsyncGenerator, Dict, List, Any, Optional
from datetime import datetime, timedelta, timezone

import hashlib
import uuid

from ..config import settings
from ..utils import (
    get_content_text, 
    load_json_file, 
    save_json_file,
    generate_machine_id,
    get_system_runtime_info,
    log_conversation
)

logger = logging.getLogger(__name__)

# AWS Event Stream 解析辅助函数
def parse_aws_event_stream_buffer(buffer: str) -> Dict:
    """解析 AWS Event Stream 格式，提取所有完整的 JSON 事件"""
    events = []
    remaining = buffer
    search_start = 0

    while True:
        # 查找可能的 JSON payload 起始位置
        content_start = remaining.find('{"content":', search_start)
        name_start = remaining.find('{"name":', search_start)
        followup_start = remaining.find('{"followupPrompt":', search_start)
        input_start = remaining.find('{"input":', search_start)
        stop_start = remaining.find('{"stop":', search_start)

        # 找到最早出现的有效 JSON 模式
        candidates = [pos for pos in [content_start, name_start, followup_start, input_start, stop_start] if pos >= 0]
        if not candidates:
            break

        json_start = min(candidates)
        if json_start < 0:
            break

        # 正确处理嵌套的 {}
        brace_count = 0
        json_end = -1
        in_string = False
        escape_next = False

        for i in range(json_start, len(remaining)):
            char = remaining[i]

            if escape_next:
                escape_next = False
                continue

            if char == '\\':
                escape_next = True
                continue

            if char == '"':
                in_string = not in_string
                continue

            if not in_string:
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        json_end = i
                        break

        if json_end < 0:
            # 不完整的 JSON，保留在缓冲区
            remaining = remaining[json_start:]
            break

        json_str = remaining[json_start:json_end + 1]
        try:
            parsed = json.loads(json_str)
            # 处理 content 事件
            if 'content' in parsed and not parsed.get('followupPrompt'):
                events.append({'type': 'content', 'data': parsed['content']})
            elif 'name' in parsed:
                events.append({'type': 'toolUse', 'data': parsed})
            elif 'input' in parsed:
                events.append({'type': 'toolUseInput', 'data': parsed})
            elif 'stop' in parsed:
                events.append({'type': 'toolUseStop', 'data': parsed})
        except json.JSONDecodeError:
            pass

        remaining = remaining[json_end + 1:]

    return {'events': events, 'remaining': remaining}

# Kiro API 常量
KIRO_CONSTANTS = {
    'REFRESH_URL': 'https://prod.{{region}}.auth.desktop.kiro.dev/refreshToken',
    'REFRESH_IDC_URL': 'https://oidc.{{region}}.amazonaws.com/token',
    'BASE_URL': 'https://codewhisperer.{{region}}.amazonaws.com/generateAssistantResponse',
    'AMAZON_Q_URL': 'https://codewhisperer.{{region}}.amazonaws.com/SendMessageStreaming',
    'USAGE_LIMITS_URL': 'https://q.{{region}}.amazonaws.com/getUsageLimits',
    'DEFAULT_MODEL_NAME': 'claude-opus-4-5',
    'USER_AGENT': 'KiroIDE',
    'KIRO_VERSION': '0.7.5',
    'CONTENT_TYPE_JSON': 'application/json',
    'AUTH_METHOD_SOCIAL': 'social',
    'CHAT_TRIGGER_TYPE_MANUAL': 'MANUAL',
    'ORIGIN_AI_EDITOR': 'AI_EDITOR',
}

# 模型映射
MODEL_MAPPING = {
    "claude-opus-4-5": "claude-opus-4.5",
    "claude-opus-4-5-20251101": "claude-opus-4.5",
    "claude-haiku-4-5": "claude-haiku-4.5",
    "claude-sonnet-4-5": "CLAUDE_SONNET_4_5_20250929_V1_0",
    "claude-sonnet-4-5-20250929": "CLAUDE_SONNET_4_5_20250929_V1_0",
    "claude-sonnet-4-20250514": "CLAUDE_SONNET_4_20250514_V1_0",
    "claude-3-7-sonnet-20250219": "CLAUDE_3_7_SONNET_20250219_V1_0"
}


class KiroApiService:
    """Kiro API 服务类"""

    def __init__(self):
        self.is_initialized = False
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.client_id: Optional[str] = None
        self.client_secret: Optional[str] = None
        self.profile_arn: Optional[str] = None
        self.auth_method = KIRO_CONSTANTS['AUTH_METHOD_SOCIAL']
        self.expires_at: Optional[datetime] = None
        self.region = 'us-east-1'
        self.machine_id: Optional[str] = None
        self.session: Optional[aiohttp.ClientSession] = None

        # 初始化凭证
        self._init_credentials()

    def _init_credentials(self):
        """初始化凭证"""
        # 优先使用 Base64 编码的凭证
        if settings.KIRO_OAUTH_CREDS_BASE64:
            try:
                import base64
                decoded_creds = base64.b64decode(settings.KIRO_OAUTH_CREDS_BASE64).decode('utf-8')
                creds = json.loads(decoded_creds)
                self._load_creds_from_dict(creds)
                logger.info('[Kiro] Successfully loaded Base64 credentials')
            except Exception as e:
                logger.error(f'[Kiro] Failed to load Base64 credentials: {e}')

        # 其次使用凭证文件
        elif settings.KIRO_OAUTH_CREDS_FILE_PATH:
            creds = load_json_file(settings.KIRO_OAUTH_CREDS_FILE_PATH)
            if creds:
                self._load_creds_from_dict(creds)
                logger.info(f'[Kiro] Successfully loaded credentials from file: {settings.KIRO_OAUTH_CREDS_FILE_PATH}')

        # 生成机器ID
        creds_dict = {
            'uuid': getattr(settings, 'uuid', None),
            'profileArn': self.profile_arn,
            'clientId': self.client_id
        }
        self.machine_id = generate_machine_id(creds_dict)

    def _load_creds_from_dict(self, creds: Dict):
        """从字典加载凭证"""
        self.access_token = creds.get('accessToken')
        self.refresh_token = creds.get('refreshToken')
        self.client_id = creds.get('clientId')
        self.client_secret = creds.get('clientSecret')
        self.profile_arn = creds.get('profileArn')

        # 解析过期时间
        expires_at = creds.get('expiresAt')
        if expires_at:
            try:
                self.expires_at = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            except:
                self.expires_at = None

        # 设置认证方法
        self.auth_method = creds.get('authMethod', KIRO_CONSTANTS['AUTH_METHOD_SOCIAL'])

    async def initialize(self):
        """初始化服务"""
        if self.is_initialized:
            return

        logger.info('[Kiro] Initializing Kiro API Service...')
        await self._ensure_token()

        # # 创建 HTTP 会话
        connector = aiohttp.TCPConnector(
            limit=100,
            limit_per_host=100,
            force_close=False
        )

        timeout = aiohttp.ClientTimeout(total=300)  # 5分钟超时

        headers = self._build_headers()

        self.session = aiohttp.ClientSession(
            connector=connector,
            headers=headers
        )

        self.is_initialized = True
        logger.info('[Kiro] Kiro API Service initialized successfully')

    async def _stream_api_real(
        self,
        method: str,
        model: str,
        body: Dict,
        is_retry: bool = False,
        retry_count: int = 0
    ) -> AsyncGenerator[Dict, None]:
        """真正的流式 API 调用"""
        if not self.is_initialized:
            await self.initialize()

        max_retries = settings.REQUEST_MAX_RETRIES
        base_delay = settings.REQUEST_BASE_DELAY / 1000  # 转换为秒

        request_data = self._build_codewhisperer_request(
            body.get('messages', []),
            model,
            body.get('tools'),
            body.get('system')
        )

        token = self.access_token
        headers = {
            'Authorization': f'Bearer {token}',
            'amz-sdk-invocation-id': str(uuid.uuid4())
        }

        request_url = self._get_request_url(model)

        # 打印请求信息
        print(f"[Kiro Stream Request] URL: {request_url}")
        print(f"[Kiro Stream Request] Headers: {json.dumps(headers, indent=2)}")
        print(f"[Kiro Stream Request] Body: {json.dumps(request_data, indent=2)}")

        stream = None
        try:
            response = await self.session.post(
                request_url,
                json=request_data,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=300)
            )

            # 打印响应头
            print(f"[Kiro Stream Response] Status: {response.status}")
            print(f"[Kiro Stream Response] Headers: {json.dumps(dict(response.headers), indent=2)}")

            stream = response.content
            buffer = ''
            last_content_event = None

            async for chunk in response.content.iter_chunked(1024):
                buffer += chunk.decode('utf-8', errors='ignore')

                # 打印响应数据块
                print(f"[Kiro Stream Response] Chunk: {chunk.decode('utf-8', errors='ignore')}")

                # 解析缓冲区中的事件
                events, remaining = parse_aws_event_stream_buffer(buffer)
                buffer = remaining

                # yield 所有事件，过滤连续重复的 content 事件
                for event in events:
                    if event.get('type') == 'content' and event.get('data'):
                        # 检查是否与上一个 content 事件完全相同
                        if last_content_event == event['data']:
                            # 跳过重复的内容
                            continue
                        last_content_event = event['data']
                        yield {'type': 'content', 'content': event['data']}
                    elif event.get('type') == 'toolUse':
                        yield {'type': 'toolUse', 'toolUse': event['data']}
                    elif event.get('type') == 'toolUseInput':
                        yield {'type': 'toolUseInput', 'input': event['data'].get('input')}
                    elif event.get('type') == 'toolUseStop':
                        yield {'type': 'toolUseStop', 'stop': event['data'].get('stop')}

        except aiohttp.ClientError as e:
            # 确保出错时关闭流
            if stream:
                stream.close()

            if e.status == 403 and not is_retry:
                logger.info('[Kiro] Received 403 in stream. Attempting token refresh and retrying...')
                await self._ensure_token(force_refresh=True)
                async for event in self._stream_api_real(method, model, body, True, retry_count):
                    yield event
                return

            if e.status == 429 and retry_count < max_retries:
                delay = base_delay * (2 ** retry_count)
                logger.info(f'[Kiro] Received 429 in stream. Retrying in {delay}s...')
                await asyncio.sleep(delay)
                async for event in self._stream_api_real(method, model, body, is_retry, retry_count + 1):
                    yield event
                return

            logger.error('[Kiro] Stream API call failed:', e)
            raise
        finally:
            # 确保流被关闭
            if stream:
                stream.close()
            timeout=timeout,
            headers=headers
        

        self.is_initialized = True
        logger.info('[Kiro] Kiro API Service initialized successfully')

    def _build_headers(self) -> Dict[str, str]:
        """构建请求头"""
        runtime_info = get_system_runtime_info()
        return {
            'Content-Type': KIRO_CONSTANTS['CONTENT_TYPE_JSON'],
            'Accept': 'application/json',
            'amz-sdk-request': 'attempt=1; max=1',
            'x-amzn-kiro-agent-mode': 'vibe',
            'x-amz-user-agent': f'aws-sdk-js/1.0.0 KiroIDE-{KIRO_CONSTANTS["KIRO_VERSION"]}-{self.machine_id}',
            'user-agent': f'aws-sdk-js/1.0.0 ua/2.1 os/{runtime_info["osName"]} lang/py md/python#{runtime_info["pythonVersion"]} api/codewhispererruntime#1.0.0 m/E KiroIDE-{KIRO_CONSTANTS["KIRO_VERSION"]}-{self.machine_id}',
            'Connection': 'close'
        }

    async def _ensure_token(self, force_refresh: bool = False):
        """确保有有效的访问令牌"""
        if self.access_token and not force_refresh and not self._is_token_expired():
            return

        if self.refresh_token:
            await self._refresh_token()
        else:
            raise ValueError('No refresh token available')

    def _is_token_expired(self) -> bool:
        """检查令牌是否过期"""
        if not self.expires_at:
            return False
        # 
        
        return datetime.now(timezone.utc) + timedelta(minutes=1) >= self.expires_at.replace(tzinfo=timezone.utc)

    async def _refresh_token(self):
        """刷新访问令牌"""
        refresh_url = KIRO_CONSTANTS['REFRESH_URL'].replace('{{region}}', self.region)

        headers = {
            'Content-Type': 'application/json',
            'User-Agent': KIRO_CONSTANTS['USER_AGENT']
        }

        body = {
            'refreshToken': self.refresh_token,
            'clientId': self.client_id,
            'clientSecret': self.client_secret
        }

        async with aiohttp.ClientSession() as session:
            # 打印请求信息
            print(f"[Kiro Token Refresh] URL: {refresh_url}")
            print(f"[Kiro Token Refresh] Headers: {json.dumps(headers, indent=2)}")
            print(f"[Kiro Token Refresh] Body: {json.dumps(body, indent=2)}")

            async with session.post(refresh_url, json=body, headers=headers) as response:
                # 打印响应信息
                print(f"[Kiro Token Refresh] Status: {response.status}")
                print(f"[Kiro Token Refresh] Headers: {json.dumps(dict(response.headers), indent=2)}")

                if response.status != 200:
                    error_text = await response.text()
                    print(f"[Kiro Token Refresh] Error: {error_text}")
                    raise Exception(f'Token refresh failed: {response.status} - {error_text}')

                data = await response.json()
                # 打印响应体
                print(f"[Kiro Token Refresh] Body: {json.dumps(data, indent=2)}")
                self.access_token = data.get('accessToken')
                if data.get('refreshToken'):
                    self.refresh_token = data.get('refreshToken')

                # 更新过期时间
                if data.get('expiresIn'):
                    self.expires_at = datetime.now(timezone.utc) + timedelta(seconds=data['expiresIn'])

                logger.info(f'[Kiro] Token refreshed successfully - Access Token: {self.access_token[:20]}...')

    def _get_request_url(self, model: str) -> str:
        """获取请求URL"""
        if model.startswith('amazonq'):
            return KIRO_CONSTANTS['AMAZON_Q_URL'].replace('{{region}}', self.region)
        return KIRO_CONSTANTS['BASE_URL'].replace('{{region}}', self.region)

    def _map_model(self, model: str) -> str:
        """映射模型名称"""
        return MODEL_MAPPING.get(model, KIRO_CONSTANTS['DEFAULT_MODEL_NAME'])

    def _build_codewhisperer_request(
        self, 
        messages: List[Dict], 
        model: str, 
        tools: Optional[List[Dict]] = None,
        system: Optional[Any] = None
    ) -> Dict:
        """构建 CodeWhisperer 请求"""
        conversation_id = str(uuid.uuid4())

        # 处理 system prompt
        system_prompt = get_content_text(system) if system else None

        # 复制消息列表以避免修改原始数据
        processed_messages = messages.copy()

        if not processed_messages:
            raise ValueError('No user messages found')

        # 判断最后一条消息是否为 assistant，如果是则移除
        if processed_messages:
            last_message = processed_messages[-1]
            if last_message.get('role') == 'assistant':
                content = last_message.get('content')
                if isinstance(content, list) and len(content) > 0:
                    first_part = content[0]
                    if first_part.get('type') == 'text' and first_part.get('text') == '{':
                        logger.info('[Kiro] Removing last assistant with "{" message from processedMessages')
                        processed_messages.pop()

        # 合并相邻相同 role 的消息
        merged_messages = []
        for i, current_msg in enumerate(processed_messages):
            if i == 0:
                merged_messages.append(current_msg)
            else:
                last_msg = merged_messages[-1]
                if current_msg.get('role') == last_msg.get('role'):
                    # 合并消息内容
                    last_content = last_msg.get('content')
                    current_content = current_msg.get('content')

                    if isinstance(last_content, list) and isinstance(current_content, list):
                        # 都是数组，合并数组内容
                        last_msg['content'].extend(current_content)
                    elif isinstance(last_content, str) and isinstance(current_content, str):
                        # 都是字符串，用换行符连接
                        last_msg['content'] = last_content + '\n' + current_content
                    elif isinstance(last_content, list) and isinstance(current_content, str):
                        # 上一条是数组，当前是字符串
                        last_msg['content'].append({'type': 'text', 'text': current_content})
                    elif isinstance(last_content, str) and isinstance(current_content, list):
                        # 上一条是字符串，当前是数组
                        last_msg['content'] = [{'type': 'text', 'text': last_content}] + current_content
                    logger.info(f'[Kiro] Merged adjacent {current_msg.get("role")} messages')
                else:
                    merged_messages.append(current_msg)

        # 用合并后的消息替换
        processed_messages = merged_messages

        # 构建工具上下文
        tools_context = {}
        if tools and isinstance(tools, list) and len(tools) > 0:
            tools_context['tools'] = [
                {
                    'toolSpecification': {
                        'name': tool.get('name'),
                        'description': tool.get('description', ''),
                        'inputSchema': {'json': tool.get('input_schema', {})}
                    }
                }
                for tool in tools
            ]

        # 构建历史记录
        codewhisperer_model = self._map_model(model)
        history = []
        start_index = 0

        # 处理 system prompt
        if system_prompt:
            if processed_messages and processed_messages[0].get('role') == 'user':
                # 如果第一条是 user 消息，将 system prompt 添加到它前面
                first_user_content = get_content_text(processed_messages[0])
                history.append({
                    'userInputMessage': {
                        'content': f'{system_prompt}\n\n{first_user_content}',
                        'modelId': codewhisperer_model,
                        'origin': KIRO_CONSTANTS['ORIGIN_AI_EDITOR']
                    }
                })
                start_index = 1
            else:
                # 否则作为独立的 user 消息
                history.append({
                    'userInputMessage': {
                        'content': system_prompt,
                        'modelId': codewhisperer_model,
                        'origin': KIRO_CONSTANTS['ORIGIN_AI_EDITOR']
                    }
                })

        # 添加剩余的 user/assistant 消息到历史记录
        for i in range(start_index, len(processed_messages) - 1):
            message = processed_messages[i]
            if message.get('role') == 'user':
                user_input_message = {
                    'content': '',
                    'modelId': codewhisperer_model,
                    'origin': KIRO_CONSTANTS['ORIGIN_AI_EDITOR']
                }
                images = []
                tool_results = []

                content = message.get('content')
                if isinstance(content, list):
                    for part in content:
                        if part.get('type') == 'text':
                            user_input_message['content'] += part.get('text', '')
                        elif part.get('type') == 'tool_result':
                            tool_results.append({
                                'content': [{'text': get_content_text(part.get('content', ''))}],
                                'status': 'success',
                                'toolUseId': part.get('tool_use_id')
                            })
                        elif part.get('type') == 'image':
                            images.append({
                                'format': part['source']['media_type'].split('/')[1],
                                'source': {'bytes': part['source']['data']}
                            })
                else:
                    user_input_message['content'] = get_content_text(message)

                # 只添加非空字段
                if images:
                    user_input_message['images'] = images
                if tool_results:
                    # 去重 toolResults
                    unique_tool_results = []
                    seen_ids = set()
                    for tr in tool_results:
                        tool_use_id = tr.get('toolUseId')
                        if tool_use_id and tool_use_id not in seen_ids:
                            seen_ids.add(tool_use_id)
                            unique_tool_results.append(tr)
                    user_input_message['userInputMessageContext'] = {'toolResults': unique_tool_results}

                history.append({'userInputMessage': user_input_message})
            elif message.get('role') == 'assistant':
                assistant_response_message = {'content': '', 'toolUses': []}

                content = message.get('content')
                if isinstance(content, list):
                    for part in content:
                        if part.get('type') == 'text':
                            assistant_response_message['content'] += part.get('text', '')
                        elif part.get('type') == 'tool_use':
                            assistant_response_message['toolUses'].append({
                                'input': part.get('input', {}),
                                'name': part.get('name'),
                                'toolUseId': part.get('id')
                            })
                else:
                    assistant_response_message['content'] = get_content_text(message)

                # 只添加非空字段
                if assistant_response_message['toolUses']:
                    history.append({'assistantResponseMessage': assistant_response_message})

        # 构建请求
        request = {
            'conversationState': {
                'chatTriggerType': KIRO_CONSTANTS['CHAT_TRIGGER_TYPE_MANUAL'],
                'conversationId': conversation_id,
                'currentMessage': {}
            }
        }

        # 添加历史记录
        if history:
            request['conversationState']['history'] = history

        # 构建当前消息
        current_message = processed_messages[-1] if processed_messages else {}
        current_content = ''
        current_tool_results = []
        current_images = []

        # 如果最后一条消息是 assistant，需要将其加入 history，然后创建一个 user 类型的 currentMessage
        if current_message.get('role') == 'assistant':
            logger.info('[Kiro] Last message is assistant, moving it to history and creating user currentMessage')

            # 构建 assistant 消息并加入 history
            assistant_response_message = {'content': '', 'toolUses': []}
            content = current_message.get('content')
            if isinstance(content, list):
                for part in content:
                    if part.get('type') == 'text':
                        assistant_response_message['content'] += part.get('text', '')
                    elif part.get('type') == 'tool_use':
                        assistant_response_message['toolUses'].append({
                            'input': part.get('input', {}),
                            'name': part.get('name'),
                            'toolUseId': part.get('id')
                        })
            else:
                assistant_response_message['content'] = get_content_text(current_message)

            if assistant_response_message['toolUses']:
                history.append({'assistantResponseMessage': assistant_response_message})

            # 设置 currentContent 为 "Continue"
            current_content = 'Continue'
        else:
            # 处理 user 消息
            content = current_message.get('content')
            if isinstance(content, list):
                for part in content:
                    if part.get('type') == 'text':
                        current_content += part.get('text', '')
                    elif part.get('type') == 'tool_result':
                        current_tool_results.append({
                            'content': [{'text': get_content_text(part.get('content', ''))}],
                            'status': 'success',
                            'toolUseId': part.get('tool_use_id')
                        })
                    elif part.get('type') == 'image':
                        current_images.append({
                            'format': part['source']['media_type'].split('/')[1],
                            'source': {'bytes': part['source']['data']}
                        })
            else:
                current_content = get_content_text(current_message)

            # Kiro API 要求 content 不能为空
            if not current_content:
                current_content = 'Tool results provided.' if current_tool_results else 'Continue'

        # 构建用户输入消息
        user_input_message = {
            'content': current_content,
            'modelId': codewhisperer_model,
            'origin': KIRO_CONSTANTS['ORIGIN_AI_EDITOR']
        }

        # 添加图片
        if current_images:
            user_input_message['images'] = current_images

        # 构建上下文
        user_input_context = {}

        # 添加工具结果（去重）
        if current_tool_results:
            unique_tool_results = []
            seen_ids = set()
            for tr in current_tool_results:
                tool_use_id = tr.get('toolUseId')
                if tool_use_id and tool_use_id not in seen_ids:
                    seen_ids.add(tool_use_id)
                    unique_tool_results.append(tr)
            user_input_context['toolResults'] = unique_tool_results

        # 添加工具定义
        if tools_context.get('tools'):
            user_input_context['tools'] = tools_context['tools']

        # 添加上下文
        if user_input_context:
            user_input_message['userInputMessageContext'] = user_input_context

        request['conversationState']['currentMessage']['userInputMessage'] = user_input_message

        # 添加 profileArn（Social Auth 需要）
        if self.auth_method == KIRO_CONSTANTS['AUTH_METHOD_SOCIAL'] and self.profile_arn:
            request['profileArn'] = self.profile_arn

        return request

    async def _call_api(
        self, 
        method: str, 
        model: str, 
        body: Dict, 
        is_retry: bool = False, 
        retry_count: int = 0 ) -> Dict:
        """调用 Kiro API"""
        if not self.is_initialized:
            await self.initialize()

        max_retries = settings.REQUEST_MAX_RETRIES
        base_delay = settings.REQUEST_BASE_DELAY / 1000  # 转换为秒

        request_url = self._get_request_url(model)
        request_data = self._build_codewhisperer_request(
            body.get('messages', []),
            model,
            body.get('tools'),
            body.get('system')
        )

        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'amz-sdk-invocation-id': str(uuid.uuid4())
        }

        # 打印请求信息
        print(f"[Kiro Request] URL: {request_url}")
        print(f"[Kiro Request] Headers: {json.dumps(headers, indent=2)}")
        print(f"[Kiro Request] Body: {json.dumps(request_data, indent=2)}")

        try:
            async with self.session.post(request_url, json=request_data, headers=headers) as response:
                # 打印响应头
                print(f"[Kiro Response] Status: {response.status}")
                print(f"[Kiro Response] Headers: {json.dumps(dict(response.headers), indent=2)}")

                if response.status == 403 and not is_retry:
                    logger.info('[Kiro] Received 403. Attempting token refresh and retrying...')
                    await self._ensure_token(force_refresh=True)
                    return await self._call_api(method, model, body, True, retry_count)

                if response.status == 429 and retry_count < max_retries:
                    delay = base_delay * (2 ** retry_count)
                    logger.info(f'[Kiro] Received 429. Retrying in {delay}s... (attempt {retry_count + 1}/{max_retries})')
                    await asyncio.sleep(delay)
                    return await self._call_api(method, model, body, is_retry, retry_count + 1)

                if 500 <= response.status < 600 and retry_count < max_retries:
                    delay = base_delay * (2 ** retry_count)
                    logger.info(f'[Kiro] Received {response.status}. Retrying in {delay}s... (attempt {retry_count + 1}/{max_retries})')
                    await asyncio.sleep(delay)
                    return await self._call_api(method, model, body, is_retry, retry_count + 1)

                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f'[Kiro] API call failed with status {response.status}: {error_text}')
                    

                # 获取原始响应数据，与JS代码保持一致
                # JS代码: const rawResponseText = Buffer.isBuffer(response.data) ? response.data.toString('utf8') : String(response.data);
                try:
                    # 检查响应头中的Content-Encoding
                    content_encoding = response.headers.get('Content-Encoding', '').lower()
                    logger.info(f'[Kiro] Content-Encoding: {content_encoding}')

                    # 先获取原始字节数据
                    raw_bytes = await response.read()
                    logger.info(f'[Kiro] Raw response length: {len(raw_bytes)} bytes')

                    # 尝试解码为UTF-8字符串
                    try:
                        response_text = raw_bytes.decode('utf-8')
                    except UnicodeDecodeError:
                        # 如果UTF-8解码失败，尝试解压gzip
                        if 'gzip' in content_encoding or raw_bytes[:2] == b'\x1f\x8b':
                            try:
                                import gzip
                                response_text = gzip.decompress(raw_bytes).decode('utf-8')
                                logger.info('[Kiro] Successfully decompressed gzip response')
                            except Exception as gzip_error:
                                logger.error(f'[Kiro] Failed to decompress gzip: {gzip_error}')
                                raise Exception('Failed to decompress gzip response')
                        else:
                            # AWS Event Stream格式 - 解析二进制协议
                            # 格式: [总长度(4字节)][头部长度(4字节)][头部数据][预签名头部(4字节)][payload]
                            response_text = self._parse_aws_event_stream(raw_bytes)

                    # 打印响应体
                    print(f"[Kiro Response] Body: {response_text}")

                    # 尝试解析JSON
                    try:
                        response_data = json.loads(response_text)
                    except json.JSONDecodeError as e:
                        logger.error(f'[Kiro] Failed to parse JSON: {e}')
                        logger.error(f'[Kiro] Response text (first 500 chars): {response_text[:500]}')
                        raise Exception(f'Failed to parse API response as JSON: {e}')

                except Exception as read_error:
                    logger.error(f'[Kiro] Failed to read response: {read_error}')
                    raise Exception(f'Failed to read API response: {read_error}')

                return response_data

        except aiohttp.ClientError as e:
            logger.error(f'[Kiro] API call failed: {e}')

    def _parse_aws_event_stream(self, raw_bytes: bytes) -> str:
        """解析AWS Event Stream格式的响应

        AWS Event Stream格式:
        [总长度(4字节)][头部长度(4字节)][头部数据][预签名头部(4字节)][payload]

        返回: 解析后的JSON字符串
        """
        import struct
        import re

        offset = 0
        events = []

        logger.info(f'[Kiro] Parsing AWS Event Stream, total bytes: {len(raw_bytes)}')
        logger.info(f'[Kiro] Raw hex (first 200 chars): {raw_bytes.hex()[:200]}')

        while offset < len(raw_bytes):
            # 保存当前事件的起始位置
            event_start = offset

            # 读取总长度（4字节，大端序）
            if offset + 4 > len(raw_bytes):
                logger.warning(f'[Kiro] Not enough bytes for total_length at offset {offset}')
                break
            total_length = struct.unpack('>I', raw_bytes[offset:offset+4])[0]
            logger.info(f'[Kiro] Event at offset {offset}: total_length={total_length}')
            offset += 4

            # 读取头部长度（4字节，大端序）
            if offset + 4 > len(raw_bytes):
                logger.warning(f'[Kiro] Not enough bytes for header_length at offset {offset}')
                break
            header_length = struct.unpack('>I', raw_bytes[offset:offset+4])[0]
            logger.info(f'[Kiro] Event at offset {offset}: header_length={header_length}')
            offset += 4

            # 跳过头部数据
            if offset + header_length > len(raw_bytes):
                logger.warning(f'[Kiro] Not enough bytes for header at offset {offset}')
                break
            logger.info(f'[Kiro] Skipping {header_length} bytes of header at offset {offset}')
            offset += header_length

            # 跳过预签名头部（4字节）
            if offset + 4 > len(raw_bytes):
                logger.warning(f'[Kiro] Not enough bytes for prelude at offset {offset}')
                break
            offset += 4

            # 读取payload
            payload_length = total_length - header_length - 12  # 12 = 4(总长度) + 4(头部长度) + 4(预签名)
            if offset + payload_length > len(raw_bytes):
                logger.warning(f'[Kiro] Not enough bytes for payload at offset {offset}')
                break

            payload = raw_bytes[offset:offset+payload_length]
            logger.info(f'[Kiro] Payload at offset {offset}: length={payload_length}, hex={payload.hex()[:100]}')

            # 跳过payload
            offset += payload_length

            # 检查是否有CRC（4字节）
            if offset + 4 <= len(raw_bytes):
                crc = struct.unpack('>I', raw_bytes[offset:offset+4])[0]
                logger.info(f'[Kiro] CRC at offset {offset}: {crc:08x}')
                offset += 4

            # 更新offset到下一个事件的开始位置
            # 每个事件的完整长度是total_length，包括所有字段
            offset = event_start + total_length
            logger.info(f'[Kiro] Moving to next event at offset {offset}')

            # 尝试将payload解码为UTF-8字符串
            try:
                # 尝试解码整个payload
                payload_text = payload.decode('utf-8')
                logger.info(f'[Kiro] Decoded payload: {payload_text[:200]}')
            except UnicodeDecodeError:
                # 如果整个payload解码失败，尝试解码前面的部分（排除可能的CRC）
                # 查找最后一个有效的JSON结束位置
                try:
                    # 尝试逐步减少payload长度，直到可以成功解码
                    for i in range(len(payload), 0, -1):
                        try:
                            payload_text = payload[:i].decode('utf-8')
                            logger.info(f'[Kiro] Decoded payload (truncated to {i} bytes): {payload_text[:200]}')
                            # 更新payload为解码成功的部分
                            payload = payload[:i]
                            break
                        except UnicodeDecodeError:
                            continue
                    else:
                        # 如果所有尝试都失败，使用errors='ignore'
                        payload_text = payload.decode('utf-8', errors='ignore')
                        logger.warning(f'[Kiro] Decoded payload with errors: {payload_text[:200]}')
                except Exception as e:
                    logger.error(f'[Kiro] Failed to decode payload: {e}')
                    raise
                # 查找JSON数据
                # Event Stream中可能包含多个JSON对象，我们需要提取它们
                # 查找 {"content": 或 {"name": 或 {"input": 等模式

                json_pattern = r'\{(?:\"content\"|\"name\"|\"input\"|\"stop\"|\"followupPrompt\")'
                matches = list(re.finditer(json_pattern, payload_text))

                for match in matches:
                    start = match.start()
                    # 找到匹配的JSON对象
                    brace_count = 0
                    in_string = False
                    escape_next = False
                    end = -1

                    for i in range(start, len(payload_text)):
                        char = payload_text[i]

                        if escape_next:
                            escape_next = False
                            continue

                        if char == '\\' and in_string:
                            escape_next = True
                            continue

                        if char == '"' and not escape_next:
                            in_string = not in_string
                            continue

                        if not in_string:
                            if char == '{':
                                brace_count += 1
                            elif char == '}':
                                brace_count -= 1
                                if brace_count == 0:
                                    end = i + 1
                                    break

                    if end > start:
                        json_str = payload_text[start:end]
                        try:
                            event_data = json.loads(json_str)
                            events.append(event_data)
                        except json.JSONDecodeError:
                            pass

            except UnicodeDecodeError:
                logger.warning(f'[Kiro] Failed to decode payload as UTF-8')
                continue

        # 将事件转换为JSON响应
        if events:
            # 如果有多个事件，合并它们
            result = {}
            content_parts = []

            for event in events:
                if 'content' in event and 'followupPrompt' not in event:
                    # 收集所有content部分
                    content_parts.append(event['content'])
                elif 'name' in event and 'toolUseId' in event:
                    if 'toolCalls' not in result:
                        result['toolCalls'] = []
                    result['toolCalls'].append({
                        'name': event['name'],
                        'toolUseId': event['toolUseId'],
                        'input': event.get('input', '{}')
                    })

            # 合并所有content部分
            if content_parts:
                result['completion'] = ''.join(content_parts)
                logger.info(f'[Kiro] Merged {len(content_parts)} content parts into completion')

            return json.dumps(result)
        else:
            # 如果没有解析到事件，尝试直接解码整个响应
            # 查找所有可能的JSON模式
            text = raw_bytes.decode('utf-8', errors='ignore')
            # 提取所有JSON对象
            json_pattern = r'\{[^{}]*\}|\{(?:[^{}]|\{[^{}]*\})*\}'
            matches = re.findall(json_pattern, text)

            for match in matches:
                try:
                    data = json.loads(match)
                    if 'completion' in data or 'content' in data:
                        return json.dumps(data)
                except json.JSONDecodeError:
                    pass

            # 如果还是没有找到，返回原始文本
            return text

    def _parse_tool_calls_from_text(self, text: str) -> List[Dict]:
        """从文本中解析工具调用"""
        tool_calls = []
        if not text or '[Called' not in text:
            return tool_calls

        # 匹配 [Called functionName with args:{...}] 格式
        pattern = r'\[Called\s+(\w+)\s+with\s+args:\s*(\{[^\]]*\})\]'
        matches = re.finditer(pattern, text)

        for match in matches:
            func_name = match.group(1)
            args_str = match.group(2)
            try:
                # 尝试修复常见的 JSON 问题
                args_str = args_str.replace(',\s*([}\]])', r'\1')
                args = json.loads(args_str)

                tool_calls.append({
                    'id': f'call_{uuid.uuid4().hex[:8]}',
                    'type': 'function',
                    'function': {
                        'name': func_name,
                        'arguments': json.dumps(args)
                    }
                })
            except json.JSONDecodeError:
                logger.warning(f'Failed to parse tool call: {args_str}')

        return tool_calls

    def _remove_tool_calls_from_text(self, text: str, tool_calls: List[Dict]) -> str:
        """从文本中移除工具调用"""
        if not tool_calls:
            return text

        result = text
        for tc in tool_calls:
            func_name = re.escape(tc['function']['name'])
            pattern = f'\[Called\s+{func_name}\s+with\s+args:\s*\{{[^\]]*\}}\]'
            result = re.sub(pattern, '', result)

        return result.strip()

    def _process_api_response(self, response: Dict) -> Dict:
        """处理 API 响应"""
        # 从响应中提取文本
        raw_text = str(response.get('completion', ''))

        # 解析工具调用
        tool_calls = self._parse_tool_calls_from_text(raw_text)

        # 从文本中移除工具调用
        clean_text = self._remove_tool_calls_from_text(raw_text, tool_calls)

        return {
            'responseText': clean_text,
            'toolCalls': tool_calls
        }

    def _build_claude_response(
        self,
        content: str,
        model: str,
        tool_calls: Optional[List[Dict]] = None,
        input_tokens: int = 0
    ) -> Dict:
        """构建 Claude 格式的响应"""
        message_id = str(uuid.uuid4())
        content_blocks = []

        # 添加文本内容块
        if content:
            content_blocks.append({
                'type': 'text',
                'text': content
            })

        # 添加工具调用块
        if tool_calls:
            for tc in tool_calls:
                content_blocks.append({
                    'type': 'tool_use',
                    'id': tc['id'],
                    'name': tc['function']['name'],
                    'input': json.loads(tc['function']['arguments'])
                })

        # 估算输出 token 数
        output_tokens = len(content) // 4
        if tool_calls:
            output_tokens += sum(len(json.dumps(tc['function']['arguments'])) // 4 for tc in tool_calls)

        return {
            'id': message_id,
            'type': 'message',
            'role': 'assistant',
            'content': content_blocks,
            'model': model,
            'stop_reason': 'tool_use' if tool_calls else 'end_turn',
            'usage': {
                'input_tokens': input_tokens,
                'output_tokens': output_tokens
            }
        }

    def _estimate_input_tokens(self, request_body: Dict) -> int:
        """估算输入 tokens"""
        total_tokens = 0

        # Count system prompt tokens
        if request_body.get('system'):
            system_text = get_content_text(request_body['system'])
            total_tokens += self._count_text_tokens(system_text)

        # Count all messages tokens
        messages = request_body.get('messages', [])
        for message in messages:
            if message.get('content'):
                content_text = get_content_text(message)
                total_tokens += self._count_text_tokens(content_text)

        # Count tools definitions tokens if present
        if request_body.get('tools'):
            total_tokens += self._count_text_tokens(json.dumps(request_body['tools']))

        return total_tokens

    def _count_text_tokens(self, text: str) -> int:
        """计算文本 tokens"""
        if not text:
            return 0
        try:
            # 使用简单的估算：每4个字符约等于1个token
            # TODO: 集成 Claude 官方 tokenizer 以获得精确计算
            return len(text) // 4
        except Exception:
            return len(text) // 4

    async def generate_content(self, model: str, request_body: Dict) -> Dict:
        """生成内容（非流式）"""
        if not self.is_initialized:
            await self.initialize()

        # 确保令牌有效

        await self._ensure_token()

        # 估算输入 tokens
        input_tokens = self._estimate_input_tokens(request_body)

        # 调用 API
        response = await self._call_api('', model, request_body)

        # 处理响应
        processed = self._process_api_response(response)

        # 构建 Claude 格式响应
        return self._build_claude_response(
            processed['responseText'],
            model,
            processed['toolCalls'],
            input_tokens=input_tokens
        )

    async def generate_content_stream(
        self, 
        model: str, 
        request_body: Dict
    ) -> AsyncGenerator[Dict, None]:
        """生成内容（流式）"""
        if not self.is_initialized:
            await self.initialize()

        # 检查 token 是否即将过期，如果是则先刷新
        if self.is_expiry_date_near():
            logger.info('[Kiro] Token is near expiry, refreshing before generateContentStream request...')
            await self._ensure_token(force_refresh=True)

        message_id = str(uuid.uuid4())
        final_model = self._map_model(model)

        # 估算输入 tokens
        input_tokens = self._estimate_input_tokens(request_body)

        # 发送 message_start 事件
        yield {
            'type': 'message_start',
            'message': {
                'id': message_id,
                'type': 'message',
                'role': 'assistant',
                'model': model,
                'usage': {'input_tokens': input_tokens, 'output_tokens': 0},
                'content': []
            }
        }

        # 发送 content_block_start 事件
        yield {
            'type': 'content_block_start',
            'index': 0,
            'content_block': {'type': 'text', 'text': ''}
        }

        # 调用 API（非流式，因为 Kiro API 本身是伪流式）
        response = await self._call_api('', model, request_body)
        processed = self._process_api_response(response)

        # 发送内容
        if processed['responseText']:
            yield {
                'type': 'content_block_delta',
                'index': 0,
                'delta': {'type': 'text_delta', 'text': processed['responseText']}
            }

        # 发送 content_block_stop 事件
        yield {'type': 'content_block_stop', 'index': 0}

        # 发送工具调用（如果有）
        if processed['toolCalls']:
            for i, tc in enumerate(processed['toolCalls']):
                block_index = i + 1

                # content_block_start
                yield {
                    'type': 'content_block_start',
                    'index': block_index,
                    'content_block': {
                        'type': 'tool_use',
                        'id': tc['id'],
                        'name': tc['function']['name'],
                        'input': {}
                    }
                }

                # content_block_delta
                yield {
                    'type': 'content_block_delta',
                    'index': block_index,
                    'delta': {
                        'type': 'input_json_delta',
                        'partial_json': tc['function']['arguments']
                    }
                }

                # content_block_stop
                yield {'type': 'content_block_stop', 'index': block_index}

        # 计算输出 tokens
        output_tokens = len(processed['responseText']) // 4
        if processed['toolCalls']:
            output_tokens += sum(len(tc['function']['arguments']) // 4 for tc in processed['toolCalls'])

        # 发送 message_delta 事件
        yield {
            'type': 'message_delta',
            'delta': {'stop_reason': 'tool_use' if processed['toolCalls'] else 'end_turn'},
            'usage': {'output_tokens': output_tokens}
        }

        # 发送 message_stop 事件
        yield {'type': 'message_stop'}

    async def close(self):
        """关闭服务"""
        if self.session:
            await self.session.close()
            self.session = None
        self.is_initialized = False


# 创建全局服务实例
_kiro_service: Optional[KiroApiService] = None


def get_kiro_service() -> KiroApiService:
    """获取 Kiro 服务实例（单例）"""
    global _kiro_service
    if _kiro_service is None:
        _kiro_service = KiroApiService()
    return _kiro_service
