# Kiro API 流式解析服务层
import json
import logging
import asyncio
import aiohttp
import uuid
from typing import AsyncGenerator, Dict, List, Any, Optional

from .kiro_base import KiroBaseService, KIRO_CONSTANTS
from ..config import settings
from ..utils import get_content_text

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
            # 准备代理参数
            proxy = None
            if settings.PROXY_SERVER:
                proxy = settings.PROXY_SERVER
                logger.info(f'[Kiro Stream] Using proxy for request: {proxy}')
            elif settings.USE_SYSTEM_PROXY_KIRO:
                logger.info('[Kiro Stream] Using system proxy for request')

            response = await self.session.post(
                request_url,
                json=request_data,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=300),
                proxy=proxy
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
