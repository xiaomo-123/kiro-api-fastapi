# Kiro API 服务层 - 主入口
import logging
from typing import AsyncGenerator, Dict, Optional

from .kiro_base import KiroBaseService
from .kiro_stream import KiroStreamService
from .kiro_nonstream import KiroNonStreamService

logger = logging.getLogger(__name__)


class KiroApiService(KiroStreamService, KiroNonStreamService):
    """Kiro API 服务类 - 整合流式和非流式功能"""

    def __init__(self):
        """初始化服务"""
        super().__init__()


# 创建全局服务实例
_kiro_service: Optional[KiroApiService] = None


def get_kiro_service() -> KiroApiService:
    """获取 Kiro 服务实例（单例）"""
    global _kiro_service
    if _kiro_service is None:
        _kiro_service = KiroApiService()
    return _kiro_service
