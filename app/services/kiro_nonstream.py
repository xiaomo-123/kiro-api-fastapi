# Kiro API 非流式解析服务层
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
import re
import uuid
import gzip
import struct
from typing import Dict, List, Any, Optional

from .kiro_base import KiroBaseService, KIRO_CONSTANTS
from ..config import settings
from ..utils import get_content_text

logger = logging.getLogger(__name__)


class KiroNonStreamService(KiroBaseService):
    """Kiro API 非流式服务类"""

    def _parse_aws_event_stream(self, raw_bytes: bytes) -> str:
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

    def _process_api_response(self, response: Optional[Dict]) -> Dict:
        """处理 API 响应
        
        Args:
            response: API 响应字典，可能为 None
            
        Returns:
            Dict: 处理后的响应字典
        """
        # 检查响应是否为 None
        if response is None:
            logger.error('[Kiro] API response is None')
            return {
                'responseText': '',
                'toolCalls': []
            }
        
        # 检查响应是否包含错误
        if 'error' in response:
            logger.error(f'[Kiro] API response contains error: {response["error"]}')
            return {
                'responseText': f'Error: {response["error"]}',
                'toolCalls': []
            }
        
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

    async def _call_api(
        self,
        method: str,
        model: str,
        body: Dict,
        is_retry: bool = False,
        retry_count: int = 0
    ) -> Dict:
        """调用 Kiro API（非流式）"""
        import time
        request_start_time = time.time()

        if not self.is_initialized:
            await self.initialize()

        max_retries = settings.REQUEST_MAX_RETRIES
        base_delay = settings.REQUEST_BASE_DELAY / 1000  # 转换为秒

        request_url = self._get_request_url(model)
        logger.info('[Kiro] Mode: 非流式 Non-streaming')
        logger.info(f'[Kiro] Request URL: {request_url}')
        logger.info(f'[Kiro] Request start time: {request_start_time}')
        request_data = self._build_codewhisperer_request(
            body.get('messages', []),
            model,
            body.get('tools'),
            body.get('system')
        )

        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'amz-sdk-invocation-id': str(uuid.uuid4()),
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "Connection": "keep-alive"
        }

        try:
            # 使用从数据库加载的代理
            proxy = self.proxy
            if proxy:
                logger.info(f'[Kiro] 非流式使用代理请求: {proxy}')

            # 使用优化的超时时间来处理高并发情况
            request_timeout = aiohttp.ClientTimeout(
                total=180,      # 3分钟总超时(优化)
                connect=15,      # 15秒连接超时(优化)
                sock_connect=10,  # 10秒socket连接超时
                sock_read=60     # 60秒读取超时(优化)
            )

            async with self.session.post(
                    request_url,
                    json=request_data,
                    headers=headers,
                    timeout=request_timeout,
                    proxy=proxy,
                    ssl=False
                ) as response:                
                        logger.info(f'[Kiro] Response status: {response.status}')
                        print(f"\n📋 响应头信息：")
                        for k, v in dict(response.headers).items():
                            print(f"  {k}: {v}")
                        
                        # ✅ 无条件打印响应内容（适配500/403/200所有状态）
                        print("\n📄 亚马逊响应内容预览（前1500字符）：")
                        try:
                            # 方案1：二进制读取 + 指定编码解码（优先latin-1，兼容所有字节，永不报错）
                            response_bytes = await response.read()  # 读取原始二进制内容（万能）
                            # 优先用latin-1解码（兼容任意字节，不会崩溃），备选utf-8/gbk
                            html_content = response_bytes.decode("latin-1", errors="ignore")
                            print(html_content[:1500])
                        except Exception as e:
                            # 方案2：容错兜底 - 强制UTF-8解码，忽略错误字节
                            html_content = await response.text(encoding="utf-8", errors="ignore")
                            print(html_content[:1500])
                        if response.status == 503:
                            logger.warning('[Kiro] Received 503 after token refresh. Disabling current account and switching...')
                            # 禁用当前账号
                            await self._disable_current_account()
                            # 切换到下一个账号
                            switched_account = await self._switch_to_next_account()
                            if switched_account:
                                # 切换账号后重新尝试
                                await self._ensure_token(force_refresh=True)
                                return await self._call_api(method, model, body, False, retry_count)
                            else:
                                # 无法切换账号，返回错误信息
                                error_text = await response.text()
                                logger.error(f'[Kiro] No available accounts to switch to. API call failed: {error_text}')
                                return {"error": f"Account suspended and no available accounts to switch: {error_text}"}
                        if response.status == 403 and not is_retry:
                            logger.info('[Kiro] Received 403. Attempting token refresh and retrying...')
                            # 刷新令牌后重新尝试
                            await self._ensure_token(force_refresh=True)
                            return await self._call_api(method, model, body, True, retry_count)
                        
                        # 如果收到403错误且已经刷新过令牌，禁用当前账号并切换到下一个账号
                        if response.status == 403 and is_retry:
                            logger.warning('[Kiro] Received 403 after token refresh. Disabling current account and switching...')
                            # 禁用当前账号
                            await self._handle_proxy_error()                    
                            await self._disable_current_account()
                            # 切换到下一个账号
                            switched_account = await self._switch_to_next_account()
                            if switched_account:
                                # 切换账号后重新尝试
                                await self._ensure_token(force_refresh=True)
                                return await self._call_api(method, model, body, False, retry_count)
                            else:
                                # 无法切换账号，返回错误信息
                                error_text = await response.text()
                                logger.error(f'[Kiro] No available accounts to switch to. API call failed: {error_text}')
                                return {"error": f"Account suspended and no available accounts to switch: {error_text}"}

                        if response.status == 429 and retry_count < max_retries:
                            delay = base_delay * (2 ** retry_count)
                            logger.info(f'[Kiro] Received 429. Retrying in {delay}s... (attempt {retry_count + 1}/{max_retries})')
                            await asyncio.sleep(delay)
                            return await self._call_api(method, model, body, is_retry, retry_count + 1)
                                        
                            
                        if 500 <= response.status < 600 and retry_count < max_retries:
                            # 如果使用代理且返回500错误，禁用当前代理
                            if self.proxy:
                                logger.warning(f'[Kiro] Received {response.status} with proxy, disabling proxy...')
                                await self._handle_proxy_error()
                                await self._disable_proxy(self.current_proxy_id)
                                await self._switch_to_next_proxy()

                            delay = base_delay * (2 ** retry_count)
                            logger.info(f'[Kiro] Received {response.status}. Retrying in {delay}s... (attempt {retry_count + 1}/{max_retries})')
                            await asyncio.sleep(delay)
                            return await self._call_api(method, model, body, is_retry, retry_count + 1)

                        if response.status != 200:
                            error_text = await response.text()
                            logger.error(f'[Kiro] API call failed with status {response.status}: {error_text}')
                            raise Exception(f'API call failed: {response.status} - {error_text}')             
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
                                    response_text = self._parse_aws_event_stream(raw_bytes)

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

                        # 记录请求处理时间
                        import time
                        request_duration = time.time() - request_start_time
                        logger.info(f'[Kiro] Request completed in {request_duration:.3f}s (model={model}, retry_count={retry_count})')

                        

                        return response_data

        except aiohttp.ClientError as e:
            error_msg = str(e)
            
            # 忽略代理服务器返回的错误（如500错误）和Invalid HTTP request错误，不打印日志
            if isinstance(e, ClientHttpProxyError) or 'Invalid HTTP request' in error_msg:
                return {"error": f"Proxy error: {error_msg}"}
            
            logger.error(f'[Kiro] API call failed: {e}')

            # 如果是代理超时错误，切换代理到下一个代理
            if 'Read timed out' in error_msg or 'timeout' in error_msg.lower():
                if await self._handle_proxy_timeout():
                    logger.info('[Kiro] Retrying with new proxy...')
                    return await self._call_api(method, model, body, is_retry, retry_count)

            # 如果是代理连接错误，优雅切换到下一个代理并重试
            if isinstance(e, (ClientProxyConnectionError, ClientConnectorError, ClientConnectorSSLError)) or 'proxy' in error_msg.lower():
                logger.warning(f'[Kiro] Proxy connection error: {e}')
                # 优雅切换到下一个代理（不标记当前代理为失败）
                if await self._handle_proxy_error():
                    await self._disable_proxy(self.current_proxy_id)
                    await self._switch_to_next_proxy()
                    logger.info('[Kiro] Retrying with new proxy...')
                    return await self._call_api(method, model, body, is_retry, retry_count)

            raise

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

    async def generate_content(self, model: str, request_body: Dict) -> Dict:
        """生成内容（非流式）"""
        if not self.is_initialized:
            await self.initialize()

        # 确保令牌有效
        await self._ensure_token()
        logger.info('[Kiro] Generating content in non-streaming mode')

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
