# Kiro 会话服务（高并发版本）
import json
import logging
import asyncio
import aiohttp
import uuid
import struct
import re
from typing import AsyncGenerator, Dict, List, Any, Optional
from datetime import datetime, timezone, timedelta

from .kiro_base import KIRO_CONSTANTS
from ..config import settings
from ..utils import get_content_text
from .account_pool_v2 import get_available_account, release_account
from .proxy_pool import get_available_proxy, release_proxy

logger = logging.getLogger(__name__)


def _parse_aws_event_stream(raw_bytes: bytes) -> str:
    """解析AWS Event Stream格式的响应"""
    offset = 0
    events = []

    while offset < len(raw_bytes):
        event_start = offset

        if offset + 4 > len(raw_bytes):
            break
        total_length = struct.unpack('>I', raw_bytes[offset:offset+4])[0]
        offset += 4

        if offset + 4 > len(raw_bytes):
            break
        header_length = struct.unpack('>I', raw_bytes[offset:offset+4])[0]
        offset += 4

        if offset + header_length > len(raw_bytes):
            break
        offset += header_length

        if offset + 4 > len(raw_bytes):
            break
        offset += 4

        payload_length = total_length - header_length - 12
        if offset + payload_length > len(raw_bytes):
            payload_length = len(raw_bytes) - offset
            if payload_length <= 0:
                break

        payload = raw_bytes[offset:offset+payload_length]
        offset += payload_length

        if offset + 4 <= len(raw_bytes):
            crc = struct.unpack('>I', raw_bytes[offset:offset+4])[0]
            offset += 4

        offset = event_start + total_length

        try:
            payload_text = payload.decode('utf-8', errors='replace')
        except Exception as e:
            logger.error(f'[Kiro Session Service] Failed to decode payload: {e}')
            payload_text = payload.decode('utf-8', errors='ignore')

        json_pattern = r'\{(?:"content"|"name"|"input"|"stop"|"followupPrompt"|"toolUseId")'
        matches = list(re.finditer(json_pattern, payload_text))

        for match in matches:
            start = match.start()
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

            if end == -1:
                end = len(payload_text)

            if end > start:
                json_str = payload_text[start:end]
                try:
                    event_data = json.loads(json_str)
                    events.append(event_data)
                except json.JSONDecodeError:
                    continue

    if events:
        result = {}
        content_parts = []
        tool_calls = []

        for event in events:
            if 'content' in event and 'followupPrompt' not in event:
                content_parts.append(event['content'])
            elif 'name' in event and 'toolUseId' in event:
                tool_calls.append({
                    'name': event['name'],
                    'toolUseId': event['toolUseId'],
                    'input': event.get('input', '{}')
                })

        if content_parts:
            result['completion'] = ''.join(content_parts)
        if tool_calls:
            result['toolCalls'] = tool_calls

        return json.dumps(result, ensure_ascii=False)
    else:
        text = raw_bytes.decode('utf-8', errors='replace')
        return text


class KiroSessionService:
    """Kiro 会话服务（高并发版本）"""

    def __init__(self):
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.client_id: Optional[str] = None
        self.client_secret: Optional[str] = None
        self.profile_arn: Optional[str] = None
        self.auth_method = KIRO_CONSTANTS['AUTH_METHOD_SOCIAL']
        self.expires_at: Optional[datetime] = None
        self.region = 'us-east-1'
        self.machine_id: Optional[str] = None
        self.proxy: Optional[str] = None
        self.current_proxy_id: Optional[int] = None
        self.proxy_type: Optional[str] = None
        self.current_account_id: Optional[int] = None
        self.conversation_id: Optional[str] = None
        self.session_history: List[Dict] = []

    def set_conversation_id(self, conversation_id: str):
        """设置会话ID"""
        self.conversation_id = conversation_id
        logger.info(f'[Kiro Session Service] Set conversation_id: {conversation_id}')
        
    def add_to_history(self, role: str, content: str):
        """添加消息到历史记录"""
        self.session_history.append({
            'role': role,
            'content': content,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        
    def get_history(self) -> List[Dict]:
        """获取历史记录"""
        return self.session_history.copy()

    async def _load_account_from_pool(self) -> Optional[Dict]:
        """从账号池获取可用账号"""
        try:
            account_data = await get_available_account()
            if not account_data:
                logger.warning('[Kiro Session Service] No available account from pool')
                return None

            account_id = account_data.get('id')
            if not account_id:
                logger.error('[Kiro Session Service] Account data missing id field')
                return None

            self.current_account_id = int(account_id)

            account_str = account_data.get('account', '')
            if not account_str or not account_str.strip():
                logger.error(f'[Kiro Session Service] Account {account_id} has empty account data')
                return None

            account_str = account_str.strip()
            try:
                account_dict = json.loads(account_str)
            except json.JSONDecodeError:
                import ast
                try:
                    account_dict = ast.literal_eval(account_str)
                except (ValueError, SyntaxError) as e:
                    logger.error(f'[Kiro Session Service] Account {account_id} failed to parse: {e}')
                    return None

            required_fields = ['accessToken', 'refreshToken', 'profileArn']
            missing_fields = [field for field in required_fields if field not in account_dict]
            if missing_fields:
                logger.error(f'[Kiro Session Service] Account {account_id} missing fields: {missing_fields}')
                return None

            return account_dict
        except Exception as e:
            logger.error(f'[Kiro Session Service] Failed to load account: {e}')
            return None

    async def _load_proxy_from_pool(self) -> Optional[str]:
        """从代理池获取可用代理"""
        try:
            proxy_data = await get_available_proxy()
            if not proxy_data:
                logger.warning('[Kiro Session Service] No available proxy from pool')
                return None

            proxy_id = proxy_data.get('id')
            if not proxy_id:
                logger.error('[Kiro Session Service] Proxy data missing id field')
                return None

            self.current_proxy_id = int(proxy_id)
            proxy_type = proxy_data.get('proxy_type', 'http').lower()
            self.proxy_type = proxy_type

            proxy_url = proxy_data.get('proxy_url', '')
            proxy_port = proxy_data.get('proxy_port', '')
            username = proxy_data.get('username', '')
            password = proxy_data.get('password', '')

            if proxy_type == 'socket' or proxy_type == 'socks5':
                proxy_url_str = "socks5://"
                proxy_url_clean = proxy_url.strip()
                if proxy_url_clean.startswith("socks5://"):
                    proxy_url_clean = proxy_url_clean[9:]
                elif proxy_url_clean.startswith("socks4://"):
                    proxy_url_clean = proxy_url_clean[9:]
                elif proxy_url_clean.startswith("http://"):
                    proxy_url_clean = proxy_url_clean[7:]
                elif proxy_url_clean.startswith("https://"):
                    proxy_url_clean = proxy_url_clean[8:]
            else:
                proxy_url_str = "http://"
                proxy_url_clean = proxy_url.strip()
                if proxy_url_clean.startswith("http://"):
                    proxy_url_clean = proxy_url_clean[7:]
                elif proxy_url_clean.startswith("https://"):
                    proxy_url_clean = proxy_url_clean[8:]

            if username and password:
                proxy_url_str += f"{username}:{password}@"
            if proxy_port:
                proxy_url_str += f"{proxy_url_clean}:{proxy_port}"
            else:
                proxy_url_str += proxy_url_clean

            return proxy_url_str
        except Exception as e:
            logger.error(f'[Kiro Session Service] Failed to load proxy: {e}')
            return None

    def _load_creds_from_dict(self, creds: Dict):
        """从字典加载凭证"""
        self.access_token = creds.get('accessToken')
        self.refresh_token = creds.get('refreshToken')
        self.client_id = creds.get('clientId')
        self.client_secret = creds.get('clientSecret')
        self.profile_arn = creds.get('profileArn')

        expires_at = creds.get('expiresAt')
        if expires_at:
            try:
                self.expires_at = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            except:
                self.expires_at = None

        self.auth_method = creds.get('authMethod', KIRO_CONSTANTS['AUTH_METHOD_SOCIAL'])

    def _generate_machine_id(self):
        """生成机器ID"""
        creds_dict = {
            'uuid': getattr(settings, 'uuid', None),
            'profileArn': self.profile_arn,
            'clientId': self.client_id
        }
        from ..utils import generate_machine_id
        self.machine_id = generate_machine_id(creds_dict)

    def _is_token_expired(self) -> bool:
        """检查令牌是否过期"""
        if not self.expires_at:
            return False
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

        timeout = aiohttp.ClientTimeout(
            total=30,
            connect=10,
            sock_connect=10,
            sock_read=30
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(refresh_url, json=body, headers=headers, ssl=False, timeout=timeout) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f'Token refresh failed: {response.status} - {error_text}')

                data = await response.json()
                self.access_token = data.get('accessToken')
                if data.get('refreshToken'):
                    self.refresh_token = data.get('refreshToken')

                if data.get('expiresIn'):
                    self.expires_at = datetime.now(timezone.utc) + timedelta(seconds=data['expiresIn'])

    async def _ensure_token(self, force_refresh: bool = False):
        """确保有有效的访问令牌"""
        if self.access_token and not force_refresh and not self._is_token_expired():
            return

        if self.refresh_token:
            await self._refresh_token()
        else:
            raise ValueError('No refresh token available')

    def _get_request_url(self, model: str) -> str:
        """获取请求URL"""
        if model.startswith('amazonq'):
            return KIRO_CONSTANTS['AMAZON_Q_URL'].replace('{{region}}', self.region)
        return KIRO_CONSTANTS['BASE_URL'].replace('{{region}}', self.region)

    def _map_model(self, model: str) -> str:
        """映射模型名称"""
        MODEL_MAPPING = {
            "claude-opus-4-5": "claude-opus-4.5",
            "claude-opus-4-5-20251101": "claude-opus-4.5",
            "claude-haiku-4-5": "claude-haiku-4.5",
            "claude-sonnet-4-5": "claude-sonnet-4-5",
            "claude-sonnet-4-5-20250929": "CLAUDE_SONNET_4_5_20250929_V1_0",
            "claude-sonnet-4-20250514": "CLAUDE_SONNET_4_20250514_V1_0",
            "claude-3-7-sonnet-20250219": "CLAUDE_3_7_SONNET_20250219_V1_0"
        }
        return MODEL_MAPPING.get(model, KIRO_CONSTANTS['DEFAULT_MODEL_NAME'])

    def _build_headers(self) -> Dict[str, str]:
        """构建请求头"""
        from ..utils import get_system_runtime_info
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

    def _build_codewhisperer_request(
        self,
        messages: List[Dict],
        model: str,
        conversation_id: str,
        history: List[Dict],
        tools: Optional[List[Dict]] = None,
        system: Optional[Any] = None
    ) -> Dict:
        """构建 CodeWhisperer 请求（会话模式）"""
        system_prompt = get_content_text(system) if system else None

        processed_messages = messages.copy()

        if not processed_messages:
            raise ValueError('No user messages found')

        last_message = processed_messages[-1]
        if last_message.get('role') != 'user':
            raise ValueError('Last message must be from user')

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

        codewhisperer_model = self._map_model(model)

        current_content = ''
        current_images = []
        current_tool_results = []

        if isinstance(last_message.get('content'), list):
            for part in last_message['content']:
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
            current_content = get_content_text(last_message)

        if not current_content:
            current_content = 'Tool results provided.' if current_tool_results else 'Continue'

        user_input_message = {
            'content': current_content,
            'modelId': codewhisperer_model,
            'origin': KIRO_CONSTANTS['ORIGIN_AI_EDITOR']
        }

        if current_images:
            user_input_message['images'] = current_images

        user_input_context = {}

        if current_tool_results:
            unique_tool_results = []
            seen_ids = set()
            for tr in current_tool_results:
                tool_use_id = tr.get('toolUseId')
                if tool_use_id and tool_use_id not in seen_ids:
                    seen_ids.add(tool_use_id)
                    unique_tool_results.append(tr)
            user_input_context['toolResults'] = unique_tool_results

        if tools_context.get('tools'):
            user_input_context['tools'] = tools_context['tools']

        if user_input_context:
            user_input_message['userInputMessageContext'] = user_input_context

        request = {
            'conversationState': {
                'chatTriggerType': KIRO_CONSTANTS['CHAT_TRIGGER_TYPE_MANUAL'],
                'conversationId': conversation_id,
                'currentMessage': {}
            }
        }

        if history:
            request['conversationState']['history'] = history

        request['conversationState']['currentMessage']['userInputMessage'] = user_input_message

        if self.auth_method == KIRO_CONSTANTS['AUTH_METHOD_SOCIAL'] and self.profile_arn:
            request['profileArn'] = self.profile_arn

        return request

    async def _call_api_stream(
        self,
        model: str,
        body: Dict,
        conversation_id: str,
        history: List[Dict]
    ) -> AsyncGenerator[Dict, None]:
        """流式 API 调用"""
        await self._ensure_token()

        request_data = self._build_codewhisperer_request(
            body.get('messages', []),
            model,
            conversation_id,
            history,
            body.get('tools'),
            body.get('system')
        )

        headers = self._build_headers()
        headers['Authorization'] = f'Bearer {self.access_token}'
        headers['amz-sdk-invocation-id'] = str(uuid.uuid4())

        request_url = self._get_request_url(model)

        proxy = self.proxy
        timeout_config = {
            'total': settings.PROXY_TOTAL_TIMEOUT * 2,
            'connect': settings.PROXY_CONNECT_TIMEOUT * 2,
            'sock_connect': settings.PROXY_CONNECT_TIMEOUT * 2,
            'sock_read': settings.PROXY_READ_TIMEOUT * 3
        }

        timeout = aiohttp.ClientTimeout(**timeout_config)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    request_url,
                    json=request_data,
                    headers=headers,
                    timeout=timeout,
                    proxy=None if self.proxy_type and self.proxy_type in ('socket', 'socks5') else proxy,
                    ssl=False if settings.PROXY_DISABLE_SSL else None
                ) as response:
                    if response.status == 503:
                        yield {"error": f"API call failed: {response.status} (Service Unavailable)"}
                        return
                    if response.status == 403:
                        logger.error(f'[Kiro Session Service] Account {self.current_account_id} failed: 403')
                        yield {"error": "403", "account_id": self.current_account_id}
                        return
                    if response.status == 400:
                        logger.error(f'[Kiro Session Service] Account {self.current_account_id} failed: 400')
                        yield {"error": "400", "account_id": self.current_account_id}
                        return
                    if response.status == 429:
                        logger.error(f'[Kiro Session Service] Account {self.current_account_id} failed: 429')
                        yield {"error": "429", "account_id": self.current_account_id}
                        return

                    if 500 <= response.status < 600:
                        logger.warning(f'[Kiro Session Service] Server error {response.status}')
                        yield {"error": f"Server error: {response.status}"}
                        return

                    if response.status != 200:
                        yield {"error": f"API call failed: {response.status}"}
                        return

                    # 流式读取响应
                    full_response = b''
                    async for chunk in response.content:
                        full_response += chunk

                    # 解析响应
                    result_text = _parse_aws_event_stream(full_response)
                    try:
                        result = json.loads(result_text)
                        if 'completion' in result:
                            yield {
                                'type': 'content_block_delta',
                                'delta': {'text': result['completion']}
                            }
                        if 'toolCalls' in result:
                            for tc in result['toolCalls']:
                                yield {
                                    'type': 'content_block_stop',
                                    'content_block': {
                                        'type': 'tool_use',
                                        'id': tc.get('toolUseId'),
                                        'name': tc.get('name'),
                                        'input': tc.get('input', {})
                                    }
                                }
                        yield {
                            'type': 'message_stop',
                            'stop_reason': 'end_turn'
                        }
                    except json.JSONDecodeError:
                        yield {
                            'type': 'content_block_delta',
                            'delta': {'text': result_text}
                        }
                        yield {
                            'type': 'message_stop',
                            'stop_reason': 'end_turn'
                        }

        except Exception as e:
            logger.error(f'[Kiro Session Service] Stream API call error: {e}')
            yield {"error": str(e)}

    async def _call_api_nonstream(
        self,
        model: str,
        body: Dict,
        conversation_id: str,
        history: List[Dict]
    ) -> Dict:
        """非流式 API 调用"""
        await self._ensure_token()

        request_data = self._build_codewhisperer_request(
            body.get('messages', []),
            model,
            conversation_id,
            history,
            body.get('tools'),
            body.get('system')
        )

        headers = self._build_headers()
        headers['Authorization'] = f'Bearer {self.access_token}'
        headers['amz-sdk-invocation-id'] = str(uuid.uuid4())

        request_url = self._get_request_url(model)

        proxy = self.proxy
        timeout = aiohttp.ClientTimeout(
            total=60,
            connect=10,
            sock_connect=10,
            sock_read=60
        )

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    request_url,
                    json=request_data,
                    headers=headers,
                    timeout=timeout,
                    proxy=None if self.proxy_type and self.proxy_type in ('socket', 'socks5') else proxy,
                    ssl=False if settings.PROXY_DISABLE_SSL else None
                ) as response:
                    if response.status == 503:
                        return {'error': 'Service Unavailable'}
                    if response.status == 403:
                        logger.error(f'[Kiro Session Service] Account {self.current_account_id} failed: 403')
                        return {'error': '403', 'account_id': self.current_account_id}
                    if response.status == 400:
                        logger.error(f'[Kiro Session Service] Account {self.current_account_id} failed: 400')
                        return {'error': '400', 'account_id': self.current_account_id}
                    if response.status == 429:
                        logger.error(f'[Kiro Session Service] Account {self.current_account_id} failed: 429')
                        return {'error': '429', 'account_id': self.current_account_id}

                    if 500 <= response.status < 600:
                        logger.warning(f'[Kiro Session Service] Server error {response.status}')
                        return {'error': f'Server error: {response.status}'}

                    if response.status != 200:
                        return {'error': f'API call failed: {response.status}'}

                    # 读取完整响应
                    full_response = await response.read()

                    # 解析响应
                    result_text = _parse_aws_event_stream(full_response)
                    try:
                        result = json.loads(result_text)
                        return result
                    except json.JSONDecodeError:
                        return {'completion': result_text}

        except Exception as e:
            logger.error(f'[Kiro Session Service] Non-stream API call error: {e}')
            return {'error': str(e)}

    async def initialize(self):
        """初始化服务"""
        account = await self._load_account_from_pool()
        if not account:
            raise ValueError('No active accounts found')

        self._load_creds_from_dict(account)
        self._generate_machine_id()

        self.proxy = await self._load_proxy_from_pool()
        if self.proxy:
            logger.info(f'[Kiro Session Service] Using proxy: {self.proxy}')
        else:
            logger.info('[Kiro Session Service] No proxy configured')

        await self._ensure_token()
