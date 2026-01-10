# Kiro 会话管理器
import logging
import asyncio
import uuid
from typing import Dict, Optional, List
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class KiroSession:
    """Kiro 会话类"""

    def __init__(self, session_id: str, account_id: int):
        self.session_id = session_id
        self.account_id = account_id
        self.conversation_id = str(uuid.uuid4())
        self.created_at = datetime.now(timezone.utc)
        self.last_used = datetime.now(timezone.utc)
        self.message_count = 0
        self.history: List[Dict] = []
        self.lock = asyncio.Lock()

    async def add_message(self, role: str, content: str):
        """添加消息到历史记录"""
        async with self.lock:
            self.history.append({
                'role': role,
                'content': content,
                'timestamp': datetime.now(timezone.utc).isoformat()
            })
            self.message_count += 1
            self.last_used = datetime.now(timezone.utc)

    async def get_history(self) -> List[Dict]:
        """获取对话历史"""
        async with self.lock:
            return self.history.copy()

    async def update_last_used(self):
        """更新最后使用时间"""
        async with self.lock:
            self.last_used = datetime.now(timezone.utc)

    def is_expired(self, timeout_minutes: int = 30) -> bool:
        """检查会话是否过期"""
        now = datetime.now(timezone.utc)
        return (now - self.last_used) > timedelta(minutes=timeout_minutes)


class KiroSessionManager:
    """Kiro 会话管理器（高并发版本）"""

    def __init__(self):
        self.sessions: Dict[str, KiroSession] = {}
        self.lock = asyncio.Lock()
        self.session_timeout_minutes = 30
        self.max_sessions = 10000  # 支持更多会话

    async def get_or_create_session(
        self, 
        session_id: Optional[str] = None,
        account_id: int = 0
    ) -> KiroSession:
        """获取或创建会话"""
        async with self.lock:
            # 清理过期会话
            await self._cleanup_expired_sessions()

            # 如果没有提供session_id，创建新会话
            if not session_id:
                session_id = f"session_{uuid.uuid4().hex[:16]}"

            # 检查会话是否已存在
            if session_id in self.sessions:
                session = self.sessions[session_id]
                await session.update_last_used()
                logger.info(f'[SessionManager] Reusing existing session: {session_id}')
                return session

            # 检查会话数量限制
            if len(self.sessions) >= self.max_sessions:
                await self._cleanup_oldest_session()

            # 创建新会话
            session = KiroSession(session_id, account_id)
            self.sessions[session_id] = session
            logger.info(f'[SessionManager] Created new session: {session_id} for account {account_id}')
            return session

    async def close_session(self, session_id: str) -> bool:
        """关闭会话"""
        async with self.lock:
            if session_id in self.sessions:
                del self.sessions[session_id]
                logger.info(f'[SessionManager] Closed session: {session_id}')
                return True
            return False

    async def _cleanup_expired_sessions(self):
        """清理过期会话"""
        now = datetime.now(timezone.utc)
        expired_sessions = [
            sid for sid, session in self.sessions.items()
            if session.is_expired(self.session_timeout_minutes)
        ]

        for sid in expired_sessions:
            del self.sessions[sid]
            logger.info(f'[SessionManager] Cleaned up expired session: {sid}')

    async def _cleanup_oldest_session(self):
        """清理最旧的会话"""
        if not self.sessions:
            return

        oldest_sid = min(
            self.sessions.keys(),
            key=lambda sid: self.sessions[sid].last_used
        )
        del self.sessions[oldest_sid]
        logger.info(f'[SessionManager] Cleaned up oldest session: {oldest_sid}')

    async def get_session_stats(self) -> Dict:
        """获取会话统计信息"""
        async with self.lock:
            total_sessions = len(self.sessions)
            total_messages = sum(s.message_count for s in self.sessions.values())
            return {
                'total_sessions': total_sessions,
                'total_messages': total_messages,
                'max_sessions': self.max_sessions,
                'session_timeout_minutes': self.session_timeout_minutes
            }

    async def get_session_info(self, session_id: str) -> Optional[Dict]:
        """获取会话信息"""
        async with self.lock:
            if session_id not in self.sessions:
                return None

            session = self.sessions[session_id]
            return {
                'session_id': session.session_id,
                'account_id': session.account_id,
                'conversation_id': session.conversation_id,
                'created_at': session.created_at.isoformat(),
                'last_used': session.last_used.isoformat(),
                'message_count': session.message_count
            }


# 全局会话管理器实例
_session_manager_instance = None


def get_session_manager() -> KiroSessionManager:
    """获取全局会话管理器实例"""
    global _session_manager_instance
    if _session_manager_instance is None:
        _session_manager_instance = KiroSessionManager()
    return _session_manager_instance
