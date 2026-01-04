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
from .account_pool import get_available_account, release_account
from .proxy_pool import get_available_proxy, release_proxy

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
        
        # 账号管理相关属性
        self.accounts_cache: List[Dict] = []  # 缓存所有可用账号
        self.current_account_index: int = 0  # 当前使用的账号索引
        self.current_account_id: Optional[int] = None  # 当前使用的账号ID
        self.account_lock = asyncio.Lock()  # 账号切换锁
        
        # 代理健康度检查相关属性
        self.proxy_health_check_task: Optional[asyncio.Task] = None  # 健康度检查任务
        self.proxy_health_check_interval: int = 300  # 健康度检查间隔（秒）
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
            
            logger.info(f'[Kiro] Successfully loaded account {account_id} from pool')
            return account_dict
        except Exception as e:
            logger.error(f'[Kiro] Failed to load account from pool: {e}')
            return None

    async def _validate_proxy_connection(self, proxy_url: str) -> bool:
        """验证代理连接是否可用
        
        Args:
            proxy_url: 代理URL
            
        Returns:
            bool: 代理是否可用
        """
        try:
            import aiohttp
           
            HEADERS = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1"
            }

            async with aiohttp.ClientSession() as session:
                try:
                    # 使用更简单的测试端点
                    async with session.get(
                        'https://www.amazon.com',
                        headers=HEADERS,
                        proxy=proxy_url,
                        ssl=False,
                        timeout=aiohttp.ClientTimeout(total=30), 
                    ) as response:
                        # 接受2xx和3xx状态码
                        if 200 <= response.status < 400:
                            logger.info(f'[Kiro] 代理测试通过: {proxy_url}')
                            return True
                        else:
                            logger.warning(f'[Kiro] Proxy returned status {response.status}: {proxy_url}')
                            return False
                except Exception as e:
                    logger.warning(f'[Kiro] Proxy connection failed: {proxy_url}, error: {e}')
                    return False
        except Exception as e:
            logger.error(f'[Kiro] Proxy validation error: {e}')
            return False

    async def _load_proxy_from_pool(self) -> Optional[str]:
        """从代理池获取可用代理"""
        try:
            # 从 Redis 代理池获取可用代理
            proxy_data = await get_available_proxy()
            if not proxy_data:
                logger.warning('[Kiro] No available proxy from pool')
                return None

            # 获取代理 ID
            proxy_id = proxy_data.get('id')
            if not proxy_id:
                logger.error('[Kiro] Proxy data missing id field')
                return None

            # 记录当前代理 ID
            self.current_proxy_id = int(proxy_id)

            # 构建完整的代理URL
            proxy_url = proxy_data.get('proxy_url', '')
            proxy_port = proxy_data.get('proxy_port', '')
            username = proxy_data.get('username', '')
            password = proxy_data.get('password', '')

            # 构建代理URL，强制使用HTTP协议以避免TLS in TLS问题
            # 清理代理URL，移除可能存在的协议前缀
            proxy_url_clean = proxy_url.strip()
            if proxy_url_clean.startswith("http://"):
                proxy_url_clean = proxy_url_clean[7:]
            elif proxy_url_clean.startswith("https://"):
                proxy_url_clean = proxy_url_clean[8:]
                logger.warning(f'[Kiro] Detected HTTPS proxy URL, converting to HTTP to avoid TLS in TLS issues')
            
            proxy_url_str = "http://"
            if username and password:
                proxy_url_str += f"{username}:{password}@"
            # 确保端口号正确地添加到主机名后面
            if proxy_port:
                proxy_url_str += f"{proxy_url_clean}:{proxy_port}"
            else:
                proxy_url_str += proxy_url_clean
            
            # 验证代理URL格式
            if not proxy_url_str.startswith("http://") and not proxy_url_str.startswith("https://"):
                logger.error(f'[Kiro] Invalid proxy URL format: {proxy_url_str}')
                return None

            # 根据配置决定是否验证代理连接
            if settings.PROXY_VALIDATE_ON_LOAD:
                if not await self._validate_proxy_connection(proxy_url_str):
                    logger.warning(f'[Kiro] Proxy connection validation failed, marking proxy {proxy_id} as unhealthy')
                    await self._disable_proxy(proxy_id)
                    return None
                logger.info(f'[Kiro] Successfully loaded and validated proxy {proxy_id} from pool: {proxy_url_str}')
            else:
                logger.info(f'[Kiro] Successfully loaded proxy {proxy_id} from pool (validation skipped): {proxy_url_str}')

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
                
                # 检查代理是否可用
                status = proxy_data.get("status", "0")
                if status == "1":
                    available_count += 1
            
            logger.info(f'[Kiro] Available proxies count: {available_count}')
            return available_count > 1
        except Exception as e:
            logger.error(f'[Kiro] Failed to check proxy count: {e}')
            return False

    async def _disable_proxy(self) -> bool:
        """禁用当前代理（从Redis代理池中标记为禁用）"""
        try:
            if self.current_proxy_id:
                # 使用 update_proxy 将代理状态设置为 0（禁用）
                
                await update_proxy(self.current_proxy_id, status="0")
                logger.info(f'[Kiro] Disabled proxy {self.current_proxy_id} in Redis pool')

            # 将代理设置为 None
            self.proxy = None
            self.current_proxy_id = None
            logger.info('[Kiro] Proxy disabled, using direct connection')
            return True
        except Exception as e:
            logger.error(f'[Kiro] Failed to disable proxy: {e}')
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
            logger.info(f'[Kiro] 恢复代理id {proxy_id} ')
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

            # 获取当前代理的评分和错误计数
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

    async def _start_proxy_health_check_loop(self):
        """启动代理健康度检查循环任务"""
        if not self.proxy_health_check_enabled:
            logger.info('[Kiro] Proxy health check is disabled')
            return

        if self.proxy_health_check_task is not None and not self.proxy_health_check_task.done():
            logger.info('[Kiro] Proxy health check task is already running')
            return

        async def health_check_loop():
            """健康度检查循环"""
            while self.proxy_health_check_enabled:
                try:
                    logger.info('[Kiro] 开始代理健康监控检测...')
                    recovered_count = await self._check_and_recover_proxies()
                    if recovered_count > 0:
                        logger.info(f'[Kiro] Health check completed, recovered {recovered_count} proxies')
                    else:
                        logger.info('[Kiro] Health check completed, no proxies recovered')
                    
                    # 等待下一次检查
                    await asyncio.sleep(self.proxy_health_check_interval)
                except asyncio.CancelledError:
                    logger.info('[Kiro] Proxy health check task cancelled')
                    break
                except Exception as e:
                    logger.error(f'[Kiro] Error in health check loop: {e}')
                    await asyncio.sleep(self.proxy_health_check_interval)

        self.proxy_health_check_task = asyncio.create_task(health_check_loop())
        logger.info('[Kiro] Proxy health check loop started')

    async def _stop_proxy_health_check(self):
        """停止代理健康度检查循环任务"""
        if self.proxy_health_check_task is None:
            return

        if not self.proxy_health_check_task.done():
            self.proxy_health_check_task.cancel()
            try:
                await self.proxy_health_check_task
            except asyncio.CancelledError:
                pass

        self.proxy_health_check_task = None
        logger.info('[Kiro] Proxy health check loop stopped')

    async def _switch_to_next_proxy(self) -> bool:
        """切换到下一个可用代理（不标记为失败）
        
        Args:
            force: 是否强制切换，不检查失败次数
        """""
        try:       
            
            # 检查是否有多个可用代理
            has_multiple = await self._has_multiple_proxies()
            if not has_multiple:
                logger.info('[Kiro] Only one proxy available, not switching')
                return False            

            # 从 Redis 代理池获取新的代理
            proxy_url = await self._load_proxy_from_pool()
            if not proxy_url:
                logger.warning('[Kiro] No available proxy from pool for switching')
                return False

            # 更新代理
            self.proxy = proxy_url
            logger.info(f'[Kiro] Successfully switched to proxy {self.current_proxy_id}')
            return True
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
            from .account_pool import update_account
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
                from .account_pool import release_account
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
    async def _check_and_recover_proxies(self) -> int:
        """检查并恢复符合条件的代理
        
        Returns:
            int: 恢复的代理数量
        """
        try:
            from .proxy_pool import redis_client
            import time
                    

            # 检查并尝试恢复被禁用的代理
            if redis_client is None:
                return 0
                # 获取所有代理键
            proxy_keys = redis_client.keys("proxy_pool:*")
            current_time = int(time.time())
            recovered_count = 0

            for key in proxy_keys:
                # 获取代理ID
                proxy_id_str = key.split(":")[-1]
                try:
                    proxy_id = int(proxy_id_str)
                except ValueError:
                    continue

                # 获取代理数据
                proxy_data = redis_client.hgetall(key)
                if not proxy_data:
                    continue                    
                # 获取评分和最后错误时间
                score = int(proxy_data.get("score", "100"))
                last_error_time = int(proxy_data.get("last_error_time", "0"))

                # 检查是否满足恢复条件
                if score > 90 and (current_time - last_error_time) > 60:
                    # 恢复代理
                    await self._enable_proxy(proxy_id)
                    # 重置最后错误时间
                    redis_client.hset(key, "last_error_time", "0")
                    logger.info(f'[Kiro] 重启代理 {key} with score={score}')
                    recovered_count += 1
        
            return recovered_count
        except Exception as e:
            logger.error(f'[Kiro] Failed to check and recover proxies: {e}')
            return 0        
    async def initialize(self):
        """初始化服务"""
        if self.is_initialized:
            return

        logger.info('[Kiro] Initializing Kiro API Service...')
        # 从 Redis 代理池加载代理
        self.proxy = await self._load_proxy_from_pool()
        # 打印代理状态
        if self.proxy:
            logger.info(f'[Kiro] Using proxy from pool: {self.proxy}')            
        else:
            logger.info('[Kiro] No proxy configured, using direct connection')          

        # 从 Redis 账号池加载账号
        account = await self._load_account_from_pool()
        if not account:
            error_msg = 'No active accounts found in database. Please add accounts to the database before starting the service.'
            logger.error(f'[Kiro] {error_msg}')
            raise ValueError(error_msg)
        
        # 使用从池中获取的账号
        self._load_creds_from_dict(account)
        logger.info(f'[Kiro] Using account from pool: {account.get("description", "N/A")}')

        # 检查是否有refresh_token
        if not self.refresh_token:
            error_msg = 'No refresh token available after loading credentials'
            logger.error(f'[Kiro] {error_msg}')
            raise ValueError(error_msg)
        
        # 生成机器ID
        self._generate_machine_id()

        await self._ensure_token()

        # 创建 HTTP 会话 - 优化连接池配置
        connector = aiohttp.TCPConnector(
            limit=1000,                   # 总连接数限制(优化)
            limit_per_host=200,          # 每个主机的连接数限制(优化)
            force_close=False,           # 启用连接复用
            enable_cleanup_closed=True,   # 启用连接清理
            ttl_dns_cache=300,           # DNS缓存5分钟
            keepalive_timeout=60        # 保持连接60秒(减少)
        )

        headers = self._build_headers()

        # 创建session时不设置timeout，让每个请求独立控制超时
        self.session = aiohttp.ClientSession(
            connector=connector,
            headers=headers
        )

        self.is_initialized = True
        logger.info('[Kiro] Kiro API Service initialized successfully')
        
        # 启动代理健康度检查循环
        await self._start_proxy_health_check_loop()

        # 记录连接池配置信息
        logger.info(f'[Kiro] Connection pool configured: '
                   f'limit={connector.limit}, '
                   f'limit_per_host={connector.limit_per_host}')

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
            await self._refresh_token()
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

        async with aiohttp.ClientSession() as session:
            # 打印请求信息
            print(f"[Kiro Token Refresh] URL: {refresh_url}")
            # print(f"[Kiro Token Refresh] Headers: {json.dumps(headers, indent=2)}")
            # print(f"[Kiro Token Refresh] Body: {json.dumps(body, indent=2)}")

            async with session.post(refresh_url, json=body, headers=headers,ssl=False,timeout=aiohttp.ClientTimeout(total=30)) as response:
                # 打印响应信息
                print(f"[Kiro Token Refresh] Status: {response.status}")
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

                logger.info(f'[Kiro] Token refreshed successfully - Access Token: {self.access_token[:20]}...')

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

    async def close(self):
        """关闭服务"""
        if self.session:
            await self.session.close()
            self.session = None
        self.is_initialized = False
