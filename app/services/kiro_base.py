# Kiro API 基础服务层
import json
import logging
import asyncio
import aiohttp
import uuid
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta, timezone

from ..config import settings
from ..utils import (
    load_json_file,
    generate_machine_id,
    get_system_runtime_info,
    get_content_text
)

logger = logging.getLogger(__name__)

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


class KiroBaseService:
    """Kiro API 基础服务类，处理认证和初始化"""

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

        # 打印代理状态
        if settings.PROXY_SERVER:
            logger.info(f'[Kiro] Using proxy: {settings.PROXY_SERVER}')
            print(f'[Kiro Proxy] Proxy enabled: {settings.PROXY_SERVER}')
        elif settings.USE_SYSTEM_PROXY_KIRO:
            logger.info('[Kiro] Using system proxy settings')
            print('[Kiro Proxy] System proxy enabled')
        else:
            logger.info('[Kiro] No proxy configured, using direct connection')
            print('[Kiro Proxy] No proxy, using direct connection')

        await self._ensure_token()

        # 创建 HTTP 会话
        connector = aiohttp.TCPConnector(
            limit=100,
            limit_per_host=100,
            force_close=False
        )

        timeout = aiohttp.ClientTimeout(total=300)  # 5分钟超时

        headers = self._build_headers()

        # 准备代理参数
        proxy = None
        if settings.PROXY_SERVER:
            proxy = settings.PROXY_SERVER
        elif settings.USE_SYSTEM_PROXY_KIRO:
            # 使用系统代理，不指定具体代理地址
            proxy = None  # aiohttp 会自动使用系统代理

        self.session = aiohttp.ClientSession(
            connector=connector,
            headers=headers,
            timeout=timeout
        )

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

        return datetime.now(timezone.utc) + timedelta(minutes=1) >= self.expires_at.replace(tzinfo=timezone.utc)

    def is_expiry_date_near(self) -> bool:
        """检查令牌是否即将过期（5分钟内）"""
        if not self.expires_at:
            return False
        return datetime.now(timezone.utc) + timedelta(minutes=5) >= self.expires_at.replace(tzinfo=timezone.utc)

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

    async def close(self):
        """关闭服务"""
        if self.session:
            await self.session.close()
            self.session = None
        self.is_initialized = False
