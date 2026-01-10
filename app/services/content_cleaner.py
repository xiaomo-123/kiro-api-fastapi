# 内容清理工具 - 用于预处理发送到 Kiro API 的内容
import re
import logging
from typing import Optional, Any

logger = logging.getLogger(__name__)


class ContentCleaner:
    """内容清理器 - 处理特殊字符、制表符、空格等"""

    @staticmethod
    def clean_text(text: str, aggressive: bool = False) -> str:
        """
        清理文本内容

        Args:
            text: 要清理的文本
            aggressive: 是否使用更激进的清理模式

        Returns:
            清理后的文本
        """
        if not text:
            return text

        original_text = text

        try:
            # 1. 移除零宽字符
            text = ContentCleaner._remove_zero_width_chars(text)

            # 2. 处理控制字符
            text = ContentCleaner._handle_control_chars(text)

            # 3. 标准化空白字符
            text = ContentCleaner._normalize_whitespace(text)

            # 4. 处理特殊字符
            text = ContentCleaner._handle_special_chars(text, aggressive)

            # 5. 修复常见的格式问题
            text = ContentCleaner._fix_common_issues(text)

            # 记录清理前后的差异
            if text != original_text:
                logger.debug(f'Content cleaned: {len(original_text)} -> {len(text)} chars')

            return text

        except Exception as e:
            logger.error(f'Error cleaning content: {e}')
            # 如果清理失败，返回原始文本
            return original_text

    @staticmethod
    def _remove_zero_width_chars(text: str) -> str:
        """移除零宽字符"""
        # 零宽字符的 Unicode 码点
        zero_width_chars = [
            '\u200b',  # 零宽空格
            '\u200c',  # 零宽不连字
            '\u200d',  # 零宽连字
            '\u2060',  # 词连接符
            '\ufeff'    # 零宽不换行空格
        ]
        for char in zero_width_chars:
            text = text.replace(char, '')
        return text

    @staticmethod
    def _handle_control_chars(text: str) -> str:
        """处理控制字符"""
        result = []
        for char in text:
            code = ord(char)
            if code < 32:
                # 保留换行符(10)、制表符(9)和回车符(13)
                if code in [10, 9, 13]:
                    result.append(char)
                # 其他控制字符替换为空格
                else:
                    result.append(' ')
            else:
                result.append(char)
        return ''.join(result)

    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        """标准化空白字符"""
        # 将多个连续空格或制表符替换为单个空格
        text = re.sub(r'[ \t]{2,}', ' ', text)

        # 将多个连续换行（超过2个）替换为2个换行
        text = re.sub(r'\n{3,}', '\n\n', text)

        # 移除行首行尾的空白
        lines = text.split('\n')
        lines = [line.strip() for line in lines]
        text = '\n'.join(lines)

        return text

    @staticmethod
    def _handle_special_chars(text: str, aggressive: bool = False) -> str:
        """处理特殊字符"""
        # 移除 Unicode 控制字符 (U+0000 to U+001F, U+007F to U+009F)
        text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', text)

        # 移除其他 Unicode 控制字符
        text = re.sub(r'[\u200b-\u200f\u2028-\u202f\u2060-\u206f\ufff0-\uffff]', '', text)

        if aggressive:
            # 激进模式：移除更多可能有问题的字符
            # 移除私有使用区字符
            text = re.sub(r'[\ue000-\uf8ff]', '', text)
            # 移除非打印字符（除了常见的标点和符号）
            text = re.sub(r'[^\x20-\x7E\n\t\r]', '', text)

        return text

    @staticmethod
    def _fix_common_issues(text: str) -> str:
        """修复常见的格式问题"""
        # 修复混合的换行符
        text = text.replace('\r\n', '\n').replace('\r', '\n')

        # 移除行尾多余空格
        lines = text.split('\n')
        lines = [line.rstrip() for line in lines]
        text = '\n'.join(lines)

        return text

    @staticmethod
    def validate_text(text: str) -> tuple[bool, Optional[str]]:
        """
        验证文本是否有效

        Args:
            text: 要验证的文本

        Returns:
            (是否有效, 错误信息)
        """
        if not text:
            return True, None

        # 检查文本长度
        if len(text) > 100000:
            return False, 'Text too long (max 100000 characters)'

        # 检查是否包含过多控制字符
        control_char_count = sum(1 for c in text if ord(c) < 32 and c not in ['\n', '\t', '\r'])
        if control_char_count > len(text) * 0.1:  # 控制字符超过10%
            return False, 'Too many control characters'

        # 检查是否包含过多零宽字符
        zero_width_count = len(re.findall(r'[\u200b\u200c\u200d\u2060\ufeff]', text))
        if zero_width_count > len(text) * 0.05:  # 零宽字符超过5%
            return False, 'Too many zero-width characters'

        return True, None


# 便捷函数
def clean_content(text: str, aggressive: bool = False) -> str:
    """清理内容的便捷函数"""
    return ContentCleaner.clean_text(text, aggressive)


def validate_content(text: str) -> tuple[bool, Optional[str]]:
    """验证内容的便捷函数"""
    return ContentCleaner.validate_text(text)


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
    return str(content) if content else ""
