
# 工具函数
import json
import hashlib
import os
import logging
from typing import Any, Dict, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


def get_content_text(content: Any) -> str:
    """从内容中提取文本"""
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        text_parts = []
        for part in content:
            if isinstance(part, dict):
                if part.get('type') == 'text':
                    text_parts.append(part.get('text', ''))
        return ''.join(text_parts)
    return str(content) if content else ''


def get_md5_hash(obj: Any) -> str:
    """生成对象的MD5哈希"""
    json_str = json.dumps(obj, sort_keys=True)
    return hashlib.md5(json_str.encode()).hexdigest()


def load_json_file(file_path: str) -> Optional[Dict]:
    """加载JSON文件"""
    try:
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load JSON file {file_path}: {e}")
    return None


def save_json_file(file_path: str, data: Dict) -> bool:
    """保存JSON文件"""
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error(f"Failed to save JSON file {file_path}: {e}")
        return False


def parse_tool_calls_from_text(text: str) -> List[Dict]:
    """从文本中解析工具调用"""
    tool_calls = []
    if not text or '[Called' not in text:
        return tool_calls

    import re
    pattern = r'\[Called\s+(\w+)\s+with\s+args:\s*(\{[^\]]*\})\]'
    matches = re.finditer(pattern, text)

    for match in matches:
        func_name = match.group(1)
        args_str = match.group(2)
        try:
            args = json.loads(args_str)
            tool_calls.append({
                'id': f"call_{hashlib.md5(func_name.encode()).hexdigest()[:8]}",
                'type': 'function',
                'function': {
                    'name': func_name,
                    'arguments': json.dumps(args)
                }
            })
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse tool call arguments: {args_str}")

    return tool_calls


def remove_tool_calls_from_text(text: str, tool_calls: List[Dict]) -> str:
    """从文本中移除工具调用"""
    if not tool_calls:
        return text

    result = text
    for tc in tool_calls:
        func_name = tc['function']['name']
        pattern = f'\[Called\s+{re.escape(func_name)}\s+with\s+args:\s*\{{[^\]]*\}}\]'
        result = re.sub(pattern, '', result)

    return result.strip()


def log_conversation(log_type: str, content: str, log_mode: str, log_filename: str):
    """记录对话日志"""
    if log_mode == 'none' or not content:
        return

    from datetime import datetime
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_entry = f"{timestamp} [{log_type.upper()}]:\n{content}\n{'-'*40}\n"

    if log_mode == 'console':
        logger.info(log_entry)
    elif log_mode == 'file':
        try:
            with open(log_filename, 'a', encoding='utf-8') as f:
                f.write(log_entry)
        except Exception as e:
            logger.error(f"Failed to write conversation log: {e}")


def generate_machine_id(credentials: Dict) -> str:
    """根据凭证生成机器ID"""
    unique_key = credentials.get('uuid') or credentials.get('profileArn') or credentials.get('clientId') or "KIRO_DEFAULT_MACHINE"
    return hashlib.sha256(unique_key.encode()).hexdigest()


def get_system_runtime_info() -> Dict[str, str]:
    """获取系统运行时信息"""
    import platform
    os_name = platform.system().lower()
    if os_name == 'windows':
        os_name = f"windows#{platform.release()}"
    elif os_name == 'darwin':
        os_name = f"macos#{platform.release()}"
    else:
        os_name = f"{os_name}#{platform.release()}"

    return {
        'osName': os_name,
        'pythonVersion': platform.python_version()
    }
