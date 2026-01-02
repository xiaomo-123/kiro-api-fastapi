# Kiro API 流式解析服务层
import json
import logging
import asyncio
import aiohttp
from aiohttp.client_exceptions import (
    ClientHttpProxyError,
    ClientProxyConnectionError,
    ClientConnectorError,
    ClientConnectorSSLError
)
import uuid
import struct
import re
import gzip
from typing import AsyncGenerator, Dict, List, Any, Optional

from .kiro_base import KiroBaseService, KIRO_CONSTANTS
from ..config import settings
from ..utils import get_content_text

logger = logging.getLogger(__name__)


def _parse_aws_event_stream(raw_bytes: bytes) -> str:
    """解析AWS Event Stream格式的响应

    AWS Event Stream格式:
    [总长度(4字节)][头部长度(4字节)][头部数据][预签名头部(4字节)][payload]

    返回: 解析后的JSON字符串
    """
    offset = 0
    events = []

    while offset < len(raw_bytes):
        # 保存当前事件的起始位置
        event_start = offset

        # 读取总长度（4字节，大端序）
        if offset + 4 > len(raw_bytes):
            logger.warning(f'[Kiro] Not enough bytes for total_length at offset {offset}')
            break
        total_length = struct.unpack('>I', raw_bytes[offset:offset+4])[0]
        offset += 4

        # 读取头部长度（4字节，大端序）
        if offset + 4 > len(raw_bytes):
            logger.warning(f'[Kiro] Not enough bytes for header_length at offset {offset}')
            break
        header_length = struct.unpack('>I', raw_bytes[offset:offset+4])[0]
        offset += 4

        # 跳过头部数据
        if offset + header_length > len(raw_bytes):
            logger.warning(f'[Kiro] Not enough bytes for header at offset {offset}')
            break
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

        # 跳过payload
        offset += payload_length

        # 检查是否有CRC（4字节）
        if offset + 4 <= len(raw_bytes):
            crc = struct.unpack('>I', raw_bytes[offset:offset+4])[0]
            offset += 4

        # 更新offset到下一个事件的开始位置
        # 每个事件的完整长度是total_length，包括所有字段
        offset = event_start + total_length

        # 尝试将payload解码为UTF-8字符串
        try:
            # 尝试解码整个payload
            payload_text = payload.decode('utf-8')
        except UnicodeDecodeError:
            # 如果整个payload解码失败，尝试解码前面的部分（排除可能的CRC）
            # 查找最后一个有效的JSON结束位置
            try:
                # 尝试逐步减少payload长度，直到可以成功解码
                for i in range(len(payload), 0, -1):
                    try:
                        payload_text = payload[:i].decode('utf-8')
                        # 更新payload为解码成功的部分
                        payload = payload[:i]
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    # 如果所有尝试都失败，使用errors='ignore'
                    payload_text = payload.decode('utf-8', errors='ignore')
            except Exception as e:
                logger.error(f'[Kiro] Failed to decode payload: {e}')
                raise
            # 查找JSON数据
            # Event Stream中可能包含多个JSON对象，我们需要提取它们
            # 查找 {"content": 或 {"name": 或 {"input": 等模式

            json_pattern = r'\{(?:"content"|"name"|"input"|"stop"|"followupPrompt")'
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


class KiroStreamService(KiroBaseService):
    """Kiro API 流式服务类"""

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

        # 获取最后一条用户消息
        last_message = processed_messages[-1]
        if last_message.get('role') != 'user':
            raise ValueError('Last message must be from user')

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

        # 处理最后一条消息的内容
        current_content = ''
        current_images = []
        current_tool_results = []

        if isinstance(last_message.get('content'), list):
            # 处理复杂的内容结构
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

        # 构建请求
        request = {
            'conversationState': {
                'chatTriggerType': KIRO_CONSTANTS['CHAT_TRIGGER_TYPE_MANUAL'],
                'conversationId': conversation_id,
                'currentMessage': {}
            }
        }

        request['conversationState']['currentMessage']['userInputMessage'] = user_input_message

        # 添加 profileArn（Social Auth 需要）
        if self.auth_method == KIRO_CONSTANTS['AUTH_METHOD_SOCIAL'] and self.profile_arn:
            request['profileArn'] = self.profile_arn

        return request

    async def _stream_api_real(
        self,
        method: str,
        model: str,
        body: Dict,
        is_retry: bool = False,
        retry_count: int = 0
    ) -> AsyncGenerator[Dict, None]:
        """真正的流式 API 调用"""
        import time
        request_start_time = time.time()

        if not self.is_initialized:
            await self.initialize()

        logger.info('[Kiro] Mode: Streaming')

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
        logger.info(f'[Kiro Stream] Request URL: {request_url}')

        try:
            # 使用从数据库加载的代理
            proxy = self.proxy
            if proxy:
                logger.info(f'[Kiro Stream] 流式使用代理请求: {proxy}')

            # 使用更长的超时时间来处理高并发情况
            request_timeout = aiohttp.ClientTimeout(
                total=300,      # 5分钟总超时
                connect=30,      # 30秒连接超时
                sock_read=120    # 120秒读取超时(增加以应对高并发)
            )

            async with self.session.post(
                request_url,
                json=request_data,
                headers=headers,
                timeout=request_timeout,
                proxy=proxy,
                ssl=False
            ) as response:
                # 打印响应状态
                logger.info(f'[Kiro Stream] Response status: {response.status}')
                if response.status == 503:
                    logger.warning('[Kiro] Received 503 after token refresh. Disabling current account and switching...')
                    # 禁用当前账号
                    await self._disable_current_account()
                    # 切换到下一个账号
                    switched_account = await self._switch_to_next_account()
                    if switched_account:
                        # 切换账号后重新尝试
                        await self._ensure_token(force_refresh=True)
                        async for event in self._stream_api_real(method, model, body, False, retry_count):
                            yield event
                        return
                    else:
                        # 无法切换账号，返回错误信息
                        error_text = await response.text()
                        logger.error(f'[Kiro] No available accounts to switch to. API call failed: {error_text}')
                        yield {"error": f"Account suspended and no available accounts to switch: {error_text}"}
                        return
                if response.status == 403 and not is_retry:
                    logger.info('[Kiro] Received 403. Attempting token refresh and retrying...')
                    # 刷新令牌后重新尝试
                    await self._ensure_token(force_refresh=True)
                    async for event in self._stream_api_real(method, model, body, True, retry_count):
                        yield event
                    return
                
                # 如果收到403错误且已经刷新过令牌，禁用当前账号并切换到下一个账号
                if response.status == 403 and is_retry:
                    logger.warning('[Kiro] Received 403 after token refresh. Disabling current account and switching...')
                    # 
                    await self._handle_proxy_error()
                    await self._disable_current_account()
                    # 切换到下一个账号
                    switched_account = await self._switch_to_next_account()
                    if switched_account:
                        # 切换账号后重新尝试
                        await self._ensure_token(force_refresh=True)
                        async for event in self._stream_api_real(method, model, body, False, retry_count):
                            yield event
                        return
                    else:
                        # 无法切换账号，返回错误信息
                        error_text = await response.text()
                        logger.error(f'[Kiro] No available accounts to switch to. API call failed: {error_text}')
                        yield {"error": f"Account suspended and no available accounts to switch: {error_text}"}
                        return

                if response.status == 429 and retry_count < max_retries:
                    delay = base_delay * (2 ** retry_count)
                    logger.info(f'[Kiro] Received 429. Retrying in {delay}s... (attempt {retry_count + 1}/{max_retries})')
                    await asyncio.sleep(delay)
                    async for event in self._stream_api_real(method, model, body, is_retry, retry_count + 1):
                        yield event
                    return
                if response.status == 500 and is_retry:                    
                    await self._handle_proxy_error()
                    await self._disable_current_account()
                    await self._switch_to_next_account()
                    
                if 500 <= response.status < 600 and retry_count < max_retries:
                    # 如果使用代理且返回500错误，处理代理错误
                    if self.proxy:
                        logger.warning(f'[Kiro] Received {response.status} with proxy, handling proxy error...')
                        await self._handle_proxy_error()
                       

                    delay = base_delay * (2 ** retry_count)
                    logger.info(f'[Kiro] Received {response.status}. Retrying in {delay}s... (attempt {retry_count + 1}/{max_retries})')
                    await asyncio.sleep(delay)
                    async for event in self._stream_api_real(method, model, body, is_retry, retry_count + 1):
                        yield event
                    return

                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f'[Kiro] API call failed with status {response.status}: {error_text}')
                    raise Exception(f'API call failed: {response.status} - {error_text}')

                # 获取原始响应数据，与JS代码保持一致
                # JS代码: const rawResponseText = Buffer.isBuffer(response.data) ? response.data.toString('utf8') : String(response.data);
                try:
                    # 检查响应头中的Content-Encoding
                    content_encoding = response.headers.get('Content-Encoding', '').lower()

                    # 先获取原始字节数据
                    raw_bytes = await response.read()

                    # 尝试解码为UTF-8字符串
                    try:
                        response_text = raw_bytes.decode('utf-8')
                    except UnicodeDecodeError:
                        # 如果UTF-8解码失败，尝试解压gzip
                        if 'gzip' in content_encoding or raw_bytes[:2] == b'\x1f\x8b':
                            try:
                                response_text = gzip.decompress(raw_bytes).decode('utf-8')
                                logger.info('[Kiro] Successfully decompressed gzip response')
                            except Exception as gzip_error:
                                logger.error(f'[Kiro] Failed to decompress gzip: {gzip_error}')
                                raise Exception('Failed to decompress gzip response')
                        else:
                            # AWS Event Stream格式 - 解析二进制协议
                            # 格式: [总长度(4字节)][头部长度(4字节)][头部数据][预签名头部(4字节)][payload]
                            response_text = _parse_aws_event_stream(raw_bytes)

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

                # 处理响应数据
                if isinstance(response_data, dict):
                    # 如果是字典，直接处理
                    if 'completion' in response_data:
                        yield {'type': 'content', 'content': response_data['completion']}
                    if 'toolCalls' in response_data:
                        for tool_call in response_data['toolCalls']:
                            yield {'type': 'toolUse', 'toolUse': tool_call}
                elif isinstance(response_data, str):
                    # 如果是字符串，尝试解析
                    try:
                        parsed = json.loads(response_data)
                        if 'completion' in parsed:
                            yield {'type': 'content', 'content': parsed['completion']}
                        if 'toolCalls' in parsed:
                            for tool_call in parsed['toolCalls']:
                                yield {'type': 'toolUse', 'toolUse': tool_call}
                    except json.JSONDecodeError:
                        # 如果解析失败，直接作为内容返回
                        yield {'type': 'content', 'content': response_data}

                # 记录请求处理时间
                import time
                request_duration = time.time() - request_start_time
                logger.info(f'[Kiro Stream] Request completed in {request_duration:.3f}s (model={model}, retry_count={retry_count})')

        except aiohttp.ClientError as e:
            error_msg = str(e)
            
            # 忽略代理服务器返回的错误（如500错误）和Invalid HTTP request错误，不打印日志
            if isinstance(e, ClientHttpProxyError) or 'Invalid HTTP request' in error_msg:
                return
            
            logger.error(f'[Kiro] API call failed: {e}')

            # 如果是代理超时错误，切换到下一个代理
            if 'Read timed out' in error_msg or 'timeout' in error_msg.lower():
                if await self._handle_proxy_timeout():
                    logger.info('[Kiro] Retrying with new proxy...')
                    async for event in self._stream_api_real(method, model, body, is_retry, retry_count):
                        yield event
                    return

            # 如果是代理连接错误，禁用当前代理并重试
            if isinstance(e, (ClientProxyConnectionError, ClientConnectorError, ClientConnectorSSLError)) or 'proxy' in error_msg.lower():
                
                    # 禁用代理后重试，不使用代理
                async for event in self._stream_api_real(method, model, body, is_retry, retry_count):
                    yield event
                return

            raise

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

        logger.info('[Kiro] Generating content in streaming mode')

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

        # 判断是否真正流式（通过检查请求中的 stream 参数）
        is_streaming = request_body.get('stream', False)

        if is_streaming:
            # 真正的流式请求 - 使用 _stream_api_real
            buffer = ''
            last_content_event = None
            tool_uses = []
            output_tokens = 0

            try:
                async for event in self._stream_api_real('', model, request_body):
                    if event.get('type') == 'content' and 'content' in event:
                        # 检查是否与上一个 content 事件完全相同
                        if last_content_event == event['content']:
                            continue
                        last_content_event = event['content']
                        output_tokens += len(event['content']) // 4
                        yield {
                            'type': 'content_block_delta',
                            'index': 0,
                            'delta': {'type': 'text_delta', 'text': event['content']}
                        }
                    elif event.get('type') == 'toolUse' and 'toolUse' in event:
                        tool_uses.append(event['toolUse'])
                    elif event.get('type') == 'toolUseInput' and 'toolUseInput' in event:
                        # 更新最后一个工具调用的输入
                        if tool_uses:
                            tool_uses[-1]['input'] = event.get('input', {})
                    elif event.get('type') == 'toolUseStop' and 'toolUseStop' in event:
                        # 工具调用完成，发送工具调用事件
                        for i, tool_use in enumerate(tool_uses):
                            block_index = i + 1
                            tool_input = tool_use.get('input', {})
                            yield {
                                'type': 'content_block_start',
                                'index': block_index,
                                'content_block': {
                                    'type': 'tool_use',
                                    'id': tool_use.get('toolUseId', str(uuid.uuid4())),
                                    'name': tool_use.get('name'),
                                    'input': tool_input
                                }
                            }
                            # 计算工具调用的 token
                            output_tokens += len(json.dumps(tool_input)) // 4
                            yield {
                                'type': 'content_block_delta',
                                'index': block_index,
                                'delta': {
                                    'type': 'input_json_delta',
                                    'partial_json': json.dumps(tool_input)
                                }
                            }
                            yield {'type': 'content_block_stop', 'index': block_index}
                        tool_uses = []

                # 发送 message_delta 事件
                yield {
                    'type': 'message_delta',
                    'delta': {'stop_reason': 'end_turn'},
                    'usage': {'output_tokens': output_tokens}
                }
            except Exception as e:
                logger.error(f'[Kiro] Streaming error: {e}')
                raise
        else:
            # 非流式请求 - 使用 _call_api
            response = await self._call_api('', model, request_body)
            processed = self._process_api_response(response)

            # 发送内容
            if processed['responseText']:
                yield {
                    'type': 'content_block_delta',
                    'index': 0,
                    'delta': {'type': 'text_delta', 'text': processed['responseText']}
                }

            # 发送工具调用事件
            if processed.get('toolCalls'):
                for i, tool_call in enumerate(processed['toolCalls']):
                    block_index = i + 1
                    tool_input = tool_call.get('input', {})
                    yield {
                        'type': 'content_block_start',
                        'index': block_index,
                        'content_block': {
                            'type': 'tool_use',
                            'id': tool_call.get('toolUseId', str(uuid.uuid4())),
                            'name': tool_call.get('name'),
                            'input': tool_input
                        }
                    }
                    yield {
                        'type': 'content_block_delta',
                        'index': block_index,
                        'delta': {
                            'type': 'input_json_delta',
                            'partial_json': json.dumps(tool_input)
                        }
                    }
                    yield {'type': 'content_block_stop', 'index': block_index}

            # 发送 message_delta 事件
            output_tokens = len(processed['responseText']) // 4 if processed['responseText'] else 0
            if processed.get('toolCalls'):
                for tool_call in processed['toolCalls']:
                    output_tokens += len(json.dumps(tool_call.get('input', {}))) // 4

            yield {
                'type': 'message_delta',
                'delta': {'stop_reason': 'end_turn'},
                'usage': {'output_tokens': output_tokens}
            }
