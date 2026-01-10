
# Kiro API 流式数据处理服务
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

# 导入必要的模块
from .kiro_base import KiroBaseService, KIRO_CONSTANTS
from ..config import settings
from .content_cleaner import clean_content, validate_content, get_content_text

logger = logging.getLogger(__name__)


def _parse_aws_event_stream(raw_bytes: bytes) -> str:
    """解析AWS Event Stream格式的响应（修复截断问题）

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
            logger.warning(f'[Kiro Stream Handler] Not enough bytes for total_length at offset {offset}')
            break
        total_length = struct.unpack('>I', raw_bytes[offset:offset+4])[0]
        offset += 4

        # 读取头部长度（4字节，大端序）
        if offset + 4 > len(raw_bytes):
            logger.warning(f'[Kiro Stream Handler] Not enough bytes for header_length at offset {offset}')
            break
        header_length = struct.unpack('>I', raw_bytes[offset:offset+4])[0]
        offset += 4

        # 跳过头部数据
        if offset + header_length > len(raw_bytes):
            logger.warning(f'[Kiro Stream Handler] Not enough bytes for header at offset {offset}')
            break
        offset += header_length

        # 跳过预签名头部（4字节）
        if offset + 4 > len(raw_bytes):
            logger.warning(f'[Kiro Stream Handler] Not enough bytes for prelude at offset {offset}')
            break
        offset += 4

        # 读取payload
        payload_length = total_length - header_length - 12  # 12 = 4(总长度) + 4(头部长度) + 4(预签名)
        if offset + payload_length > len(raw_bytes):
            logger.warning(f'[Kiro Stream Handler] Not enough bytes for payload at offset {offset}, need {payload_length}, have {len(raw_bytes)-offset}')
            # 修复：使用剩余所有字节，避免完全丢失
            payload_length = len(raw_bytes) - offset
            if payload_length <= 0:
                break

        payload = raw_bytes[offset:offset+payload_length]
        offset += payload_length

        # 检查是否有CRC（4字节）
        if offset + 4 <= len(raw_bytes):
            crc = struct.unpack('>I', raw_bytes[offset:offset+4])[0]
            offset += 4

        # 严格按总长度移动offset，避免错位
        offset = event_start + total_length

        # 优化Payload解码逻辑，保留完整数据
        try:
            # 使用replace模式保留所有字节，避免截断
            payload_text = payload.decode('utf-8', errors='replace')
        except Exception as e:
            logger.error(f'[Kiro Stream Handler] Failed to decode payload: {e}')
            payload_text = payload.decode('utf-8', errors='ignore')

        # 优化JSON匹配逻辑，支持嵌套
        # 查找所有可能的JSON起始位置
        json_pattern = r'\{(?:"content"|"name"|"input"|"stop"|"followupPrompt"|"toolUseId")'
        matches = list(re.finditer(json_pattern, payload_text))

        for match in matches:
            start = match.start()
            brace_count = 0
            in_string = False
            escape_next = False
            end = -1

            # 遍历整个文本找闭合括号，支持嵌套
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

            # 如果没找到闭合括号，使用到文本末尾
            if end == -1:
                end = len(payload_text)

            if end > start:
                json_str = payload_text[start:end]
                try:
                    event_data = json.loads(json_str)
                    events.append(event_data)
                except json.JSONDecodeError as e:
                    logger.debug(f'[Kiro Stream Handler] Invalid JSON segment: {json_str[:100]} | Error: {e}')
                    # 尝试修复简单的JSON错误（如末尾逗号）
                    try:
                        json_str_fixed = re.sub(r',\s*}', '}', json_str)
                        json_str_fixed = re.sub(r',\s*]', ']', json_str_fixed)
                        event_data = json.loads(json_str_fixed)
                        events.append(event_data)
                    except:
                        continue

    # 合并事件并返回
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
        # 返回原始文本而非空，保留更多信息
        text = raw_bytes.decode('utf-8', errors='replace')
        # 尝试提取所有可能的JSON
        json_pattern = r'\{[^{}]*\}|\{(?:[^{}]|\{[^{}]*\})*\}'
        matches = re.findall(json_pattern, text)
        for match in matches:
            try:
                data = json.loads(match)
                if 'completion' in data or 'content' in data:
                    return json.dumps(data, ensure_ascii=False)
            except:
                continue
        return text


class KiroStreamHandler(KiroBaseService):
    """Kiro API 流式数据处理服务类"""

    async def stream_api_request(
        self,
        method: str,
        model: str,
        body: Dict,
        retry_count: int = 0,
        max_retries: int = 3
    ) -> AsyncGenerator[Dict, None]:
        """流式API请求处理，专门处理流式数据并返回403和429错误"""
        if retry_count >= max_retries:
            logger.error(f'[Kiro Stream Handler] Max retries ({max_retries}) reached')
            yield {"error": "Max retries reached"}
            return

        if not self.is_initialized:
            await self.initialize()

        # 构建请求数据
        request_data = self._build_codewhisperer_request(
            body.get('messages', []),
            model,
            body.get('tools'),
            body.get('system')
        )

        token = self.access_token
        headers = {
            'Authorization': f'Bearer {token}',
            'amz-sdk-invocation-id': str(uuid.uuid4()),
            'Accept-Encoding': 'gzip, deflate'
        }

        request_url = self._get_request_url(model)

        try:
            # 使用从代理池获取的代理
            proxy = self.proxy
            if proxy:
                logger.info(f'[Kiro Stream Handler] 使用代理请求: {proxy} (类型: {self.proxy_type}, 索引: {self.current_proxy_index})')
            else:
                logger.info('[Kiro Stream Handler] 使用直连请求')

            # 根据请求类型使用不同的超时配置
            is_streaming = body.get('stream', False)
            if is_streaming:
                # 流式请求使用更长的超时时间
                timeout_config = {
                    'total': settings.STREAM_TOTAL_TIMEOUT,
                    'connect': settings.STREAM_CONNECT_TIMEOUT,
                    'sock_connect': settings.STREAM_CONNECT_TIMEOUT,
                    'sock_read': settings.STREAM_READ_TIMEOUT
                }
            else:
                # 非流式请求使用较短的超时时间
                timeout_config = {
                    'total': settings.NONSTREAM_TOTAL_TIMEOUT,
                    'connect': settings.NONSTREAM_CONNECT_TIMEOUT,
                    'sock_connect': settings.NONSTREAM_CONNECT_TIMEOUT,
                    'sock_read': settings.NONSTREAM_READ_TIMEOUT
                }

            if self.proxy_type and self.proxy_type in ('socket', 'socks5'):
                request_timeout = aiohttp.ClientTimeout(**timeout_config)
            else:
                request_timeout = aiohttp.ClientTimeout(**timeout_config)

            async with self.session.post(
                request_url,
                json=request_data,
                headers=headers,
                timeout=request_timeout,
                proxy=None if self.proxy_type and self.proxy_type in ('socket', 'socks5') else proxy,
                ssl=False if settings.PROXY_DISABLE_SSL else None
            ) as response:
                # 状态码处理 - 确保正确返回403和429错误
                if response.status == 503:
                    logger.error(f'[Kiro Stream Handler] API call failed: {response.status} (Service Unavailable)')
                    yield {"error": "503", "account_id": self.current_account_id}
                    return

                if response.status == 403:
                    logger.error(f'[Kiro Stream Handler] Account {self.current_account_id} failed: {response.status} - Forbidden')
                    yield {"error": "403", "account_id": self.current_account_id}
                    return

                if response.status == 400:
                    logger.error(f'[Kiro Stream Handler] Account {self.current_account_id} failed: {response.status} - Bad Request')
                    yield {"error": "400", "account_id": self.current_account_id}
                    return

                if response.status == 429:
                    logger.error(f'[Kiro Stream Handler] Account {self.current_account_id} failed: {response.status} - Too Many Requests')
                    yield {"error": "429", "account_id": self.current_account_id}
                    return

                if 500 <= response.status < 600:
                    logger.warning(f'[Kiro Stream Handler] Server error {response.status}, retrying... (retry {retry_count+1}/{max_retries})')
                    if self.proxy and await self._switch_to_next_proxy():
                        await asyncio.sleep(1)  # 稍长延迟避免频繁重试
                        async for event in self.stream_api_request(method, model, body, retry_count + 1):
                            yield event
                    return

                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f'[Kiro Stream Handler] Account {self.current_account_id} failed: {response.status} - {error_text}')
                    yield {"error": f"API call failed: {response.status} - {error_text}"}
                    return

                # 处理响应数据
                try:
                    # 检查响应压缩
                    content_encoding = response.headers.get('Content-Encoding', '').lower()
                    raw_bytes = await response.read()

                    # Gzip解压（增强容错）
                    if 'gzip' in content_encoding or raw_bytes[:2] == b'\x1f\x8b':
                        try:
                            raw_bytes = gzip.decompress(raw_bytes)
                        except Exception as gzip_error:
                            logger.error(f'[Kiro Stream Handler] Failed to decompress gzip: {gzip_error}')
                            # 返回原始数据而非直接失败
                            yield {"warning": "Failed to decompress gzip, using raw data"}

                    # 解析AWS Event Stream
                    response_text = _parse_aws_event_stream(raw_bytes)

                    # 解析JSON（增强容错）
                    try:
                        response_data = json.loads(response_text)
                    except json.JSONDecodeError as e:
                        logger.warning(f'[Kiro Stream Handler] Invalid JSON response: {e}')
                        # 尝试提取有效内容
                        response_data = {
                            'completion': response_text,
                            'error': 'Invalid JSON format'
                        }

                except Exception as read_error:
                    logger.error(f'[Kiro Stream Handler] Failed to read response: {read_error}')
                    yield {"error": f"Failed to read API response: {str(read_error)[:100]}"}
                    return

                # 处理响应数据
                if isinstance(response_data, dict):
                    if 'completion' in response_data:
                        yield {'type': 'content', 'content': response_data['completion']}
                    if 'toolCalls' in response_data:
                        for tool_call in response_data['toolCalls']:
                            yield {'type': 'toolUse', 'toolUse': tool_call}
                elif isinstance(response_data, str):
                    yield {'type': 'content', 'content': response_data}

        except aiohttp.ClientError as e:
            error_msg = str(e)
            logger.error(f'[Kiro Stream Handler] API call failed: {e}')

            # 超时处理
            if 'Read timed out' in error_msg or 'timeout' in error_msg.lower():
                await self._handle_proxy_error()
                has_multiple = await self._has_multiple_proxies()
                if has_multiple and retry_count < max_retries:
                    if await self._handle_proxy_timeout():
                        await asyncio.sleep(1)
                        async for event in self.stream_api_request(method, model, body, retry_count + 1):
                            yield event
                        return
                yield {"error": f"Request timeout: {error_msg}"}
                return

            # 代理连接错误处理
            if isinstance(e, (ClientProxyConnectionError, ClientConnectorError, ClientConnectorSSLError)) or 'proxy' in error_msg.lower():
                await self._handle_proxy_error()
                has_multiple = await self._has_multiple_proxies()
                if not has_multiple:
                    # 单代理时重试
                    if retry_count < max_retries:
                        await asyncio.sleep(1)
                        async for event in self.stream_api_request(method, model, body, retry_count + 1):
                            yield event
                        return
                else:
                    # 多代理时切换
                    if await self._switch_to_next_proxy() and retry_count < max_retries:
                        await asyncio.sleep(1)
                        async for event in self.stream_api_request(method, model, body, retry_count + 1):
                            yield event
                        return

            yield {"error": f"API call failed: {error_msg[:100]}"}
            return

    async def generate_content_stream(
        self,
        model: str,
        request_body: Dict
    ) -> AsyncGenerator[Dict, None]:
        """生成内容（流式）- 确保正确处理403和429错误"""
        if not self.is_initialized:
            await self.initialize()

        # 检查 token 是否即将过期
        if self.is_expiry_date_near():
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

        # 判断是否真正流式
        is_streaming = request_body.get('stream', False)

        if is_streaming:
            # 优化流式数据拼接，移除错误的重复过滤
            full_content = ""  # 保存完整内容
            last_content = ""  # 记录上一次的增量
            tool_uses = []
            output_tokens = 0

            try:
                async for event in self.stream_api_request('', model, request_body):
                    # 检查是否包含错误（特别是403和429）
                    if isinstance(event, dict) and 'error' in event:
                        error_msg = event.get('error', '')
                        # 直接返回错误，让controller处理
                        yield event
                        return
                        
                    if event.get('type') == 'content' and 'content' in event:
                        current_content = event['content']
                        # 计算增量而非全量对比（避免重复过滤）
                        if current_content.startswith(full_content):
                            delta = current_content[len(full_content):]
                        else:
                            delta = current_content
                            full_content = current_content  # 重置全量

                        if delta:  # 只有增量不为空时才发送
                            last_content = delta
                            output_tokens += len(delta) // 4
                            full_content += delta  # 累积完整内容
                            yield {
                                'type': 'content_block_delta',
                                'index': 0,
                                'delta': {'type': 'text_delta', 'text': delta}
                            }
                    elif event.get('type') == 'toolUse' and 'toolUse' in event:
                        tool_uses.append(event['toolUse'])
                    elif event.get('type') == 'toolUseInput' and 'toolUseInput' in event:
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

                # 确保发送完整的结束事件
                yield {'type': 'content_block_stop', 'index': 0}
                yield {
                    'type': 'message_delta',
                    'delta': {'stop_reason': 'end_turn'},
                    'usage': {'output_tokens': output_tokens}
                }
                yield {'type': 'message_stop'}

            except Exception as e:
                logger.error(f'[Kiro Stream Handler] Streaming error: {e}', exc_info=True)
                # 发送错误事件而非直接抛出
                yield {'type': 'error', 'error': str(e)[:200]}
        else:
            # 非流式请求（保持原有逻辑）
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

            # 发送结束事件
            if processed['responseText']:
                yield {'type': 'content_block_stop', 'index': 0}

            output_tokens = len(processed['responseText']) // 4 if processed['responseText'] else 0
            if processed.get('toolCalls'):
                for tool_call in processed['toolCalls']:
                    output_tokens += len(json.dumps(tool_call.get('input', {}))) // 4

            yield {
                'type': 'message_delta',
                'delta': {'stop_reason': 'end_turn'},
                'usage': {'output_tokens': output_tokens}
            }
            yield {'type': 'message_stop'}ompress(raw_bytes)
                        except Exception as gzip_error:
                            logger.error(f'[Kiro Stream Handler] Failed to decompress gzip: {gzip_error}')
                            yield {"warning": "Failed to decompress gzip, using raw data"}

                    # 解析AWS Event Stream
                    response_text = _parse_aws_event_stream(raw_bytes)

                    # 解析JSON（增强容错）
                    try:
                        response_data = json.loads(response_text)
                    except json.JSONDecodeError as e:
                        logger.warning(f'[Kiro Stream Handler] Invalid JSON response: {e}')
                        response_data = {
                            'completion': response_text,
                            'error': 'Invalid JSON format'
                        }

                except Exception as read_error:
                    logger.error(f'[Kiro Stream Handler] Failed to read response: {read_error}')
                    yield {"error": f"Failed to read API response: {str(read_error)[:100]}"}
                    return

                # 处理响应数据
                if isinstance(response_data, dict):
                    if 'completion' in response_data:
                        yield {'type': 'content', 'content': response_data['completion']}
                    if 'toolCalls' in response_data:
                        for tool_call in response_data['toolCalls']:
                            yield {'type': 'toolUse', 'toolUse': tool_call}
                elif isinstance(response_data, str):
                    yield {'type': 'content', 'content': response_data}

        except aiohttp.ClientError as e:
            error_msg = str(e)
            logger.error(f'[Kiro Stream Handler] API call failed: {e}')

            # 超时处理
            if 'Read timed out' in error_msg or 'timeout' in error_msg.lower():
                await self._handle_proxy_error()
                has_multiple = await self._has_multiple_proxies()
                if has_multiple and retry_count < max_retries:
                    if await self._handle_proxy_timeout():
                        await asyncio.sleep(1)
                        async for event in self.stream_api_request(method, model, body, retry_count + 1):
                            yield event
                        return
                yield {"error": f"Request timeout: {error_msg}"}
                return

            # 代理连接错误处理
            if isinstance(e, (ClientProxyConnectionError, ClientConnectorError, ClientConnectorSSLError)) or 'proxy' in error_msg.lower():
                await self._handle_proxy_error()
                has_multiple = await self._has_multiple_proxies()
                if not has_multiple:
                    # 单代理时重试
                    if retry_count < max_retries:
                        await asyncio.sleep(1)
                        async for event in self.stream_api_request(method, model, body, retry_count + 1):
                            yield event
                        return
                else:
                    # 多代理时切换
                    if await self._switch_to_next_proxy() and retry_count < max_retries:
                        await asyncio.sleep(1)
                        async for event in self.stream_api_request(method, model, body, retry_count + 1):
                            yield event
                        return

            yield {"error": f"API call failed: {error_msg[:100]}"}
            return
