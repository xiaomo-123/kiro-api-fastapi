# Kiro API 基础服务层
import json
import logging
import asyncio
import aiohttp
import uuid
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta, timezone
from .proxy_pool import update_proxy
from ..config import settings
from .proxy_pool import redis_client
import time   
from ..utils import (
    load_json_file,
    generate_machine_id,
    get_system_runtime_info,
    get_content_text
)
import urllib3
from .account_pool_v2 import get_available_account, release_account
from .proxy_pool import get_available_proxy, release_proxy
from ..db.database import AsyncSessionLocal
from sqlalchemy import select
try:
    from aiohttp_socks import ProxyConnector, ProxyType
    SOCKS5_AVAILABLE = True
except ImportError:
    SOCKS5_AVAILABLE = False

logger = logging.getLogger(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
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

    # 类级别的锁，确保只有一个服务实例运行代理健康检查循环
    _proxy_health_check_lock = asyncio.Lock()
    _proxy_health_check_running = False

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
        self.proxy: Optional[str] = None
        self.current_proxy_id: Optional[int] = None  # 当前使用的代理ID
        self.proxy_type: Optional[str] = None  # 当前使用的代理类型
        
        # 代理池管理相关属性
        self.proxy_pool: List[Dict] = []  # 代理池列表
        self.current_proxy_index: int = 0  # 当前使用的代理索引
        self.proxy_pool_initialized: bool = False  # 代理池是否已初始化

        # 账号管理相关属性
        self.accounts_cache: List[Dict] = []  # 缓存所有可用账号
        self.current_account_index: int = 0  # 当前使用的账号索引
        self.current_account_id: Optional[int] = None  # 当前使用的账号ID
        self.account_lock = asyncio.Lock()  # 账号切换锁
        
        # 代理健康度检查相关属性
        self.proxy_health_check_task: Optional[asyncio.Task] = None  # 健康度检查任务
        self.proxy_health_check_interval: int = 30  # 健康度检查间隔（秒）
        self.proxy_health_check_enabled: bool = True  # 是否启用健康度检查

      
    def _generate_machine_id(self):
        """生成机器ID"""
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

    async def _load_account_from_pool(self) -> Optional[Dict]:
        """从账号池获取可用账号"""
        try:
            # 从 Redis 账号池获取可用账号
            account_data = await get_available_account()
            if not account_data:
                logger.warning('[Kiro] No available account from pool')
                return None
            
            # 获取账号 ID
            account_id = account_data.get('id')
            if not account_id:
                logger.error('[Kiro] Account data missing id field')
                return None
            
            # 记录当前账号 ID
            self.current_account_id = int(account_id)
            
            # 解析账号数据
            account_str = account_data.get('account', '')
            if not account_str or not account_str.strip():
                logger.error(f'[Kiro] Account {account_id} has empty account data')
                return None
            
            # 尝试解析JSON，支持单引号和双引号
            account_str = account_str.strip()
            try:
                # 首先尝试标准JSON解析
                account_dict = json.loads(account_str)
            except json.JSONDecodeError:
                # 如果失败，尝试使用ast.literal_eval解析（支持单引号）
                import ast
                try:
                    account_dict = ast.literal_eval(account_str)
                except (ValueError, SyntaxError) as e:
                    logger.error(f'[Kiro] Account {account_id} failed to parse with both json and ast: {e}')
                    return None
            
            # 验证必需字段
            required_fields = ['accessToken', 'refreshToken', 'profileArn']
            missing_fields = [field for field in required_fields if field not in account_dict]
            if missing_fields:
                logger.error(f'[Kiro] Account {account_id} missing required fields: {missing_fields}')
                return None
            
            # 验证expiresAt字段（如果存在）
            if 'expiresAt' in account_dict:
                try:
                    datetime.fromisoformat(account_dict['expiresAt'].replace('Z', '+00:00'))
                except Exception as e:
                    logger.warning(f'[Kiro] Account {account_id} has invalid expiresAt: {e}')
            
            return account_dict
        except Exception as e:
            logger.error(f'[Kiro] Failed to load account from pool: {e}')
            return None

    async def _initialize_proxy_pool(self) -> bool:
        """初始化代理池，从数据库加载所有可用代理"""
        try:
            # 从数据库加载所有可用代理
            async with AsyncSessionLocal() as db:
                from ..db.models import Proxy
                stmt = select(Proxy).filter(Proxy.status == "1")
                result = await db.execute(stmt)
                proxies = result.scalars().all()

                if not proxies:
                    logger.warning('[Kiro] No enabled proxies found in database')
                    return False

                # 构建代理池列表
                self.proxy_pool = []
                for proxy in proxies:
                    proxy_dict = {
                        'id': proxy.id,
                        'proxy_type': proxy.proxy_type,
                        'proxy_url': proxy.proxy_url,
                        'proxy_port': proxy.proxy_port,
                        'username': proxy.username or '',
                        'password': proxy.password or ''
                    }
                    self.proxy_pool.append(proxy_dict)

                # 重置代理索引
                self.current_proxy_index = 0
                self.proxy_pool_initialized = True

                return True
        except Exception as e:
            logger.error(f'[Kiro] Failed to initialize proxy pool: {e}')
            return False

    async def _load_proxy_from_pool(self) -> Optional[str]:
        """从代理池获取可用代理"""
        try:
            # 如果代理池未初始化，先初始化
            if not self.proxy_pool_initialized:
                await self._initialize_proxy_pool()

            # 如果代理池为空，返回None
            if not self.proxy_pool:
                logger.warning('[Kiro] Proxy pool is empty')
                return None

            
            # 获取当前索引的代理
            proxy_data = self.proxy_pool[self.current_proxy_index]

            # 获取代理 ID
            proxy_id = proxy_data.get('id')
            if not proxy_id:
                logger.error('[Kiro] Proxy data missing id field')
                return None

            # 记录当前代理 ID
            self.current_proxy_id = int(proxy_id)

            # 获取代理类型
            proxy_type = proxy_data.get('proxy_type', 'http').lower()
            self.proxy_type = proxy_type  # 保存代理类型

            # 构建完整的代理URL
            proxy_url = proxy_data.get('proxy_url', '')
            proxy_port = proxy_data.get('proxy_port', '')
            username = proxy_data.get('username', '')
            password = proxy_data.get('password', '')

            # 根据代理类型构建代理URL
            if proxy_type == 'socket' or proxy_type == 'socks5':
                # Socket/SOCKS5代理
                proxy_url_str = "socks5://"
                # 清理代理URL，移除可能存在的协议前缀
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
                # HTTP/HTTPS代理
                proxy_url_str = "http://"
                # 清理代理URL，移除可能存在的协议前缀
                proxy_url_clean = proxy_url.strip()
                if proxy_url_clean.startswith("http://"):
                    proxy_url_clean = proxy_url_clean[7:]
                elif proxy_url_clean.startswith("https://"):
                    proxy_url_clean = proxy_url_clean[8:]
                    logger.warning(f'[Kiro] Detected HTTPS proxy URL, converting to HTTP to avoid TLS in TLS issues')

            
            
            # 添加认证信息
            if username and password:
                proxy_url_str += f"{username}:{password}@"
            # 确保端口号正确地添加到主机名后面
            if proxy_port:
                proxy_url_str += f"{proxy_url_clean}:{proxy_port}"
            else:
                proxy_url_str += proxy_url_clean
            
            # 验证代理URL格式
            valid_protocols = ["http://", "https://", "socks4://", "socks5://"]
            if not any(proxy_url_str.startswith(protocol) for protocol in valid_protocols):
                logger.error(f'[Kiro] Invalid proxy URL format: {proxy_url_str}')
                return None

            #
            return proxy_url_str
        except Exception as e:
            logger.error(f'[Kiro] Failed to load proxy from pool: {e}')
            return None

    async def _has_multiple_proxies(self) -> bool:
        """检查是否有多个可用代理"""
        try:
            
            if redis_client is None:
                return False
            
            # 获取所有代理键
            proxy_keys = redis_client.keys("proxy_pool:*")
            available_count = 0
            
            for key in proxy_keys:
                # 获取代理数据
                proxy_data = redis_client.hgetall(key)
                if not proxy_data:
                    continue
                
                # 检查代理是否可用（状态为1或2都表示可用）
                status = proxy_data.get("status", "0")
                if status in ("1", "2"):
                    available_count += 1
            
            return available_count > 1
        except Exception as e:
            logger.error(f'[Kiro] Failed to check proxy count: {e}')
            return False

    async def _enable_proxy(self, proxy_id: int) -> bool:
        """启用指定代理（从Redis代理池中标记为启用）

        Args:
            proxy_id: 要启用的代理ID

        Returns:
            bool: 是否成功启用代理
        """
        try:            
            await update_proxy(proxy_id, status="1")  
            return True
        except Exception as e:
            logger.error(f'[Kiro] Failed to enable proxy id {proxy_id}: {e}')
            return False

    
    async def _handle_proxy_error(self) -> bool:
        """处理代理错误，增加错误计数，更新评分，并尝试切换到下一个代理"""
        try:
            if not self.current_proxy_id:
                return False

            
            if redis_client is None:
                return False

            import time
            proxy_key = f"proxy_pool:{self.current_proxy_id}"          
            current_score = int(redis_client.hget(proxy_key, "score") or "100")           
        
            # 原子性地增加错误计数
            new_error_count = redis_client.hincrby(proxy_key, "error_count", 1)

            # 只在第一次错误时设置 last_error_time（初始化功能）
            if new_error_count == 1:
                redis_client.hset(proxy_key, "last_error_time", str(int(time.time())))

            # 计算新的评分：score = score - error_count
            new_score = max(0, current_score - new_error_count)
            redis_client.hset(proxy_key, "score", str(new_score))                                 
            return True
        except Exception as e:
            logger.error(f'[Kiro] Failed to handle proxy error: {e}')
            return False
    def _handle_proxy_error1(self) -> bool:
        """处理代理错误，增加错误计数，更新评分，并尝试切换到下一个代理"""
        try:
            if not self.current_proxy_id:
                return False

            
            if redis_client is None:
                return False

            import time
            proxy_key = f"proxy_pool:{self.current_proxy_id}"            

            current_score = int(redis_client.hget(proxy_key, "score") or "100")
            error_count = int(redis_client.hget(proxy_key, "error_count") or "0")
        
            # 增加错误计数
            error_count += 1
            redis_client.hset(proxy_key, "error_count", str(error_count))

            # 初始化记录最后错误时间（如果为空才设置）
            last_error_time = redis_client.hget(proxy_key, "last_error_time")
            if not last_error_time:
                redis_client.hset(proxy_key, "last_error_time", str(int(time.time())))

            # 计算新的评分：score = score - error_count
            new_score = max(0, current_score - error_count)
            redis_client.hset(proxy_key, "score", str(new_score))                                 
            return True
        except Exception as e:
            logger.error(f'[Kiro] Failed to handle proxy error: {e}')
            return False

    
    
    async def _switch_to_next_proxy(self) -> bool:
        """切换到下一个可用代理（不标记为失败）
        
        Args:
            force: 是否强制切换，不检查失败次数
        """""
        try:       
            
            # 如果代理池未初始化或为空，先初始化
            if not self.proxy_pool_initialized or not self.proxy_pool:
                await self._initialize_proxy_pool()

            # 如果代理池仍然为空，返回False
            if not self.proxy_pool:
                logger.warning('[Kiro] Proxy pool is empty, cannot switch proxy')
                return False            
            # 计算下一个代理索引（循环遍历）
            self.current_proxy_index = (self.current_proxy_index + 1) % len(self.proxy_pool)

            # 获取下一个代理
            proxy_data = self.proxy_pool[self.current_proxy_index]
            proxy_id = proxy_data.get('id')

            # 构建代理URL
            proxy_url = self._build_proxy_url(proxy_data)

            # 更新代理
            self.proxy = proxy_url
            self.current_proxy_id = int(proxy_id)
            self.proxy_type = proxy_data.get('proxy_type', 'http').lower()

            #logger.info(f'[Kiro] Switched proxy from {previous_proxy_id} to {self.current_proxy_id} (index: {self.current_proxy_index})')
        except Exception as e:
            logger.error(f'[Kiro] Failed to switch to next proxy: {e}')
            return False

    def _build_proxy_url(self, proxy_data: Dict) -> str:
        """构建代理URL

        Args:
            proxy_data: 代理数据字典

        Returns:
            str: 代理URL
        """
        try:
            # 获取代理类型和URL
            proxy_type = proxy_data.get('proxy_type', 'http').lower()
            proxy_url = proxy_data.get('proxy_url', '')
            proxy_port = proxy_data.get('proxy_port', '')
            username = proxy_data.get('username', '')
            password = proxy_data.get('password', '')

            # 根据代理类型构建代理URL
            if proxy_type == 'socket' or proxy_type == 'socks5':
                # Socket/SOCKS5代理
                proxy_url_str = "socks5://"
                # 清理代理URL，移除可能存在的协议前缀
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
                # HTTP/HTTPS代理
                proxy_url_str = "http://"
                # 清理代理URL，移除可能存在的协议前缀
                proxy_url_clean = proxy_url.strip()
                if proxy_url_clean.startswith("http://"):
                    proxy_url_clean = proxy_url_clean[7:]
                elif proxy_url_clean.startswith("https://"):
                    proxy_url_clean = proxy_url_clean[8:]
                    logger.warning(f'[Kiro] Detected HTTPS proxy URL, converting to HTTP to avoid TLS in TLS issues')

            # 添加认证信息
            if username and password:
                proxy_url_str += f"{username}:{password}@"
            # 确保端口号正确地添加到主机名后面
            if proxy_port:
                proxy_url_str += f"{proxy_url_clean}:{proxy_port}"
            else:
                proxy_url_str += proxy_url_clean

            # 验证代理URL格式
            valid_protocols = ["http://", "https://", "socks4://", "socks5://"]
            if not any(proxy_url_str.startswith(protocol) for protocol in valid_protocols):
                logger.error(f'[Kiro] Invalid proxy URL format: {proxy_url_str}')
                return None

            return proxy_url_str
        except Exception as e:
            logger.error(f'[Kiro] Failed to build proxy URL: {e}')
            return None
        except Exception as e:
            logger.error(f'[Kiro] Failed to switch to next proxy: {e}')
            return False

    async def _handle_proxy_timeout(self) -> bool:
        """处理代理超时错误，自动切换到下一个代理

        Returns:
            bool: 是否成功切换到新代理
        """
        if not self.proxy:
            return False

        # 检查是否有多个可用代理
        has_multiple = await self._has_multiple_proxies()
        if not has_multiple:
            logger.info('[Kiro] Only one proxy available, reusing current proxy')
            # 不释放代理，直接返回True表示继续使用当前代理
            return True

        logger.warning('[Kiro] Proxy timeout detected, switching to next proxy...')
        await release_proxy(self.current_proxy_id, success=False)
        switched = await self._switch_to_next_proxy()
        if switched:
            logger.info('[Kiro] Successfully switched to new proxy')
        return switched

    async def _disable_current_account(self) -> bool:
        """禁用当前账号"""
        try:
            # 获取当前账号 ID
            if not self.current_account_id:
                logger.warning('[Kiro] No current account ID available to disable account')
                return False

            # 从 Redis 账号池中禁用账号
            logger.info(f'[Kiro] Disabling account {self.current_account_id}')
            
            # 使用 update_account 函数将账号状态设置为 0（禁用）
            from .account_pool_v2 import update_account
            await update_account(self.current_account_id, status="0")
            
            logger.info(f'[Kiro] Successfully disabled account {self.current_account_id}')
            return True
        except Exception as e:
            logger.error(f'[Kiro] Failed to disable current account: {e}')
            return False

    
    async def _switch_to_next_account(self) -> bool:
        """切换到下一个可用账号"""
        async with self.account_lock:
            # 释放当前账号（标记为失败）
            if self.current_account_id:
                from .account_pool_v2 import release_account
                await release_account(self.current_account_id, success=False)
                logger.info(f'[Kiro] Released account {self.current_account_id} with failure status')

            # 从 Redis 账号池获取新的账号
            account = await self._load_account_from_pool()
            if not account:
                logger.warning('[Kiro] No available account from pool for switching')
                return False         
         
            
            # 直接使用从Redis获取的账号数据，不再使用内存缓存
                
            logger.info(f'[Kiro] Switching to account {self.current_account_id} from Redis pool')
                
                # 加载新账号的凭证
            self._load_creds_from_dict(account)
            
            # 重新生成机器ID
            creds_dict = {
                'uuid': getattr(settings, 'uuid', None),
                'profileArn': self.profile_arn,
                'clientId': self.client_id
            }
            self.machine_id = generate_machine_id(creds_dict)
            
            # 重置令牌过期状态
            self.expires_at = None
            
            return True                       
    
   
    async def initialize(self):
        """初始化服务"""
        if self.is_initialized:
            return

        # logger.info('[Kiro] Initializing Kiro API Service...')
        
        # 释放之前可能持有的代理和账号资源
        if self.current_proxy_id:
            try:
                await release_proxy(self.current_proxy_id, success=True)
                logger.info(f'[Kiro] Released previous proxy {self.current_proxy_id}')
            except Exception as e:
                logger.warning(f'[Kiro] Failed to release previous proxy: {e}')
            self.current_proxy_id = None
            self.proxy = None
        
        if self.current_account_id:
            try:
                await release_account(self.current_account_id, success=True)
                logger.info(f'[Kiro] Released previous account {self.current_account_id}')
            except Exception as e:
                logger.warning(f'[Kiro] Failed to release previous account: {e}')
            self.current_account_id = None
        # 从 Redis 代理池加载代理（带重试机制）
        # 直接加载代理，不需要重试（新的代理池已经优化）
        self.proxy = await self._load_proxy_from_pool()
        # 打印代理状态
        if self.proxy:
            logger.info(f'[Kiro] Using proxy from pool: {self.proxy} (类型: {self.proxy_type})')            
        else:
            logger.info('[Kiro] No proxy configured, using direct connection')          

        # 从 Redis 账号池加载账号
        account = await self._load_account_from_pool()
        if not account:
            error_msg = 'No active accounts found in database. Please add accounts to the database before starting the service.'
            logger.error(f'[Kiro] {error_msg}')
            # 不直接抛出异常，而是标记为未初始化
            # 这样可以让服务继续运行，等待账号添加
            self.is_initialized = False
            return False
        
        # 使用从池中获取的账号
        self._load_creds_from_dict(account)
        # logger.info(f'[Kiro] Using account from pool: {account.get("description", "N/A")}')

        # 检查是否有refresh_token
        if not self.refresh_token:
            error_msg = 'No refresh token available after loading credentials'
            logger.error(f'[Kiro] {error_msg}')
            raise ValueError(error_msg)
        
        # 生成机器ID
        self._generate_machine_id()

        # 创建 HTTP 会话 - 优化连接池配置
        # 注意：必须在_ensure_token之前创建session，因为_refresh_token需要使用session
        # 根据代理类型创建相应的连接器
        if self.proxy and self.proxy_type in ('socket', 'socks5') and SOCKS5_AVAILABLE:
            # 使用SOCKS5代理
            try:
                # 解析代理URL以获取主机和端口
                proxy_url_clean = self.proxy.replace('socks5://', '').replace('socks4://', '')
                if '@' in proxy_url_clean:
                    # 有认证信息
                    auth_part, addr_part = proxy_url_clean.split('@', 1)
                    username, password = auth_part.split(':', 1) if ':' in auth_part else (auth_part, '')
                    proxy_host, proxy_port = addr_part.rsplit(':', 1) if ':' in addr_part else (addr_part, 1080)
                else:
                    # 无认证信息
                    username, password = '', ''
                    proxy_host, proxy_port = proxy_url_clean.rsplit(':', 1) if ':' in proxy_url_clean else (proxy_url_clean, 1080)

                logger.info(f'[Kiro] Using SOCKS5 proxy connector: {proxy_host}:{proxy_port}')
                connector = ProxyConnector(
                    proxy_type=ProxyType.SOCKS5,
                    host=proxy_host,
                    port=int(proxy_port),
                    username=username,
                    password=password,
                    limit=500,  # 减少总连接数限制，避免资源占用过多
                    limit_per_host=100,  # 减少每主机连接数限制，提高并发性能
                    force_close=False,
                    enable_cleanup_closed=True,
                    ttl_dns_cache=300,
                    keepalive_timeout=30,  # 减少保持连接时间，加快连接回收
                    rdns=True,  # 使用远程DNS解析
                    socks5_remote_dns=True  # SOCKS5远程DNS解析
                )
            except Exception as e:
                logger.error(f'[Kiro] Failed to create SOCKS5 connector: {e}, falling back to TCP connector')
                connector = aiohttp.TCPConnector(
                    limit=500,  # 减少总连接数限制，避免资源占用过多
                    limit_per_host=100,  # 减少每主机连接数限制，提高并发性能
                    force_close=False,
                    enable_cleanup_closed=True,
                    ttl_dns_cache=300,
                    keepalive_timeout=30  # 减少保持连接时间，加快连接回收
                )
        else:
            # 使用HTTP代理或直连
            from ..config import settings
            connector = aiohttp.TCPConnector(
                limit=settings.KIRO_SESSION_POOL_SIZE,        # 总连接数限制: 1000
                limit_per_host=settings.KIRO_SESSION_LIMIT_PER_HOST,  # 每个主机的连接数限制: 200
                force_close=False,           # 启用连接复用
                enable_cleanup_closed=True,   # 启用连接清理
                ttl_dns_cache=300,           # DNS缓存5分钟
                keepalive_timeout=settings.KIRO_SESSION_KEEPALIVE_TIMEOUT  # 保持连接60秒
            )

        headers = self._build_headers()

        # 创建session时不设置timeout，让每个请求独立控制超时
        self.session = aiohttp.ClientSession(
            connector=connector,
            headers=headers
        )

        # 现在session已创建，可以安全地调用_ensure_token
        await self._ensure_token()

        self.is_initialized = True
        
        # 记录连接池配置信息
        # logger.info(f'[Kiro] Connection pool configured: '
        #            f'limit={connector.limit}, '
        #            f'limit_per_host={connector.limit_per_host}')

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
            # 添加重试机制，最多重试3次，每次间隔递增
            max_retries = 3
            retry_delay = 2  # 初始延迟2秒

            for attempt in range(max_retries):
                try:
                    await self._refresh_token()
                    return  # 成功刷新token，直接返回
                except Exception as e:
                    error_msg = str(e)
                    # 如果是429错误，且不是最后一次尝试，则等待后重试
                    if '429' in error_msg and attempt < max_retries - 1:
                        logger.warning(f'[Kiro] Token refresh rate limited (attempt {attempt + 1}/{max_retries}), retrying after {retry_delay} seconds...')
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2  # 指数退避
                    else:
                        # 其他错误或最后一次尝试失败，抛出异常
                        logger.error(f'[Kiro] Token refresh failed after {attempt + 1} attempts: {error_msg}')
                        raise
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

        # 使用已有的session，并优化超时时间
        timeout = aiohttp.ClientTimeout(
            total=30,  # 优化到30秒，提高响应速度
            connect=10,
            sock_connect=10,
            sock_read=30
        )     

        async with self.session.post(refresh_url, json=body, headers=headers, ssl=False, timeout=timeout) as response:
                # 打印响应信息
                # print(f"[Kiro Token Refresh] Status: {response.status}")
                # print(f"[Kiro Token Refresh] Headers: {json.dumps(dict(response.headers), indent=2)}")

                if response.status != 200:
                    error_text = await response.text()
                    print(f"[Kiro Token Refresh] Error: {error_text}")
                    raise Exception(f'Token refresh failed: {response.status} - {error_text}')

                data = await response.json()
                # 打印响应体
                # print(f"[Kiro Token Refresh] Body: {json.dumps(data, indent=2)}")
                self.access_token = data.get('accessToken')
                if data.get('refreshToken'):
                    self.refresh_token = data.get('refreshToken')

                # 更新过期时间
                if data.get('expiresIn'):
                    self.expires_at = datetime.now(timezone.utc) + timedelta(seconds=data['expiresIn'])

                # logger.info(f'[Kiro] Token refreshed successfully - Access Token: {self.access_token[:20]}...')

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
                        #logger.info('[Kiro] Removing last assistant with "{" message from processedMessages')
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
                    #logger.info(f'[Kiro] Merged adjacent {current_msg.get("role")} messages')
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
            #logger.info('[Kiro] Last message is assistant, moving it to history and creating user currentMessage')

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
