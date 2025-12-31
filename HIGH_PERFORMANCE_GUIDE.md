# Kiro API 高性能优化指南

## 目标
实现 /claude-kiro-oauth/v1/messages 接口每秒处理200次请求的能力。

## 当前架构分析

### 现有组件
1. **FastAPI应用**：处理HTTP请求
2. **Kiro服务层**：处理与Kiro API的交互
3. **数据库层**：SQLite存储账号和API密钥
4. **负载均衡**：Nginx + 多实例部署

### 性能瓶颈
1. **数据库访问**：每个请求都查询数据库验证API密钥
2. **同步日志记录**：日志记录使用同步I/O
3. **单实例处理**：单个实例处理能力有限
4. **连接池限制**：aiohttp连接池限制为100

## 优化方案

### 1. API密钥缓存优化

**问题**：每个请求都查询数据库验证API密钥，造成数据库压力

**解决方案**：实现API密钥缓存机制

```python
# app/routes/messages.py
from functools import lru_cache
from datetime import datetime, timedelta

# API密钥缓存
api_key_cache = {}
cache_expiry = {}

async def verify_authorization(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
) -> None:
    """验证授权（带缓存）"""
    api_key = None

    # 检查 Bearer token
    if authorization and authorization.startswith('Bearer '):
        api_key = authorization[7:]
    # 检查 x-api-key 头
    elif x_api_key:
        api_key = x_api_key

    if not api_key:
        raise HTTPException(
            status_code=401,
            detail={
                'type': 'error',
                'error': {
                    'type': 'authentication_error',
                    'message': 'Unauthorized: API key is missing.'
                }
            }
        )

    # 检查缓存
    if api_key in api_key_cache:
        expiry_time = cache_expiry.get(api_key)
        if expiry_time and datetime.now() < expiry_time:
            if api_key_cache[api_key]:
                return  # 缓存命中且有效
            else:
                raise HTTPException(
                    status_code=401,
                    detail={
                        'type': 'error',
                        'error': {
                            'type': 'authentication_error',
                            'message': 'Unauthorized: API key is invalid.'
                        }
                    }
                )

    # 查询数据库
    db = next(get_db())
    try:
        valid_api_key = db.query(ApiKey).filter(
            ApiKey.api_key == api_key,
            ApiKey.status == '1'
        ).first()

        # 更新缓存（缓存5分钟）
        api_key_cache[api_key] = bool(valid_api_key)
        cache_expiry[api_key] = datetime.now() + timedelta(minutes=5)

        if valid_api_key:
            return
    finally:
        db.close()

    # 检查全局API密钥
    if api_key != settings.REQUIRED_API_KEY:
        raise HTTPException(
            status_code=401,
            detail={
                'type': 'error',
                'error': {
                    'type': 'authentication_error',
                    'message': 'Unauthorized: API key is invalid or missing.'
                }
            }
        )
```

### 2. 异步日志记录优化

**问题**：日志记录使用同步I/O，阻塞请求处理

**解决方案**：使用异步日志记录

```python
# app/utils.py
import asyncio
from queue import Queue
from threading import Thread
import logging

class AsyncLogger:
    """异步日志记录器"""

    def __init__(self):
        self.queue = Queue()
        self.worker_thread = Thread(target=self._worker, daemon=True)
        self.worker_thread.start()

    def _worker(self):
        """工作线程，处理日志写入"""
        while True:
            log_type, content, log_mode, log_filename = self.queue.get()
            if log_mode == 'none' or not content:
                continue

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

            self.queue.task_done()

    def log(self, log_type: str, content: str, log_mode: str, log_filename: str):
        """异步记录日志"""
        self.queue.put((log_type, content, log_mode, log_filename))

# 创建全局异步日志记录器实例
async_logger = AsyncLogger()

def log_conversation_async(log_type: str, content: str, log_mode: str, log_filename: str):
    """异步记录对话日志"""
    async_logger.log(log_type, content, log_mode, log_filename)
```

### 3. 连接池优化

**问题**：aiohttp连接池限制为100，可能成为瓶颈

**解决方案**：增加连接池大小并优化配置

```python
# app/services/kiro_base.py
async def initialize(self):
    """初始化服务"""
    if self.is_initialized:
        return

    logger.info('[Kiro] Initializing Kiro API Service...')

    # 从数据库加载代理
    self.proxy = self._load_proxy_from_db()

    # 从数据库加载账号
    accounts = self._load_accounts_from_db()
    if not accounts:
        error_msg = 'No active accounts found in database. Please add accounts to the database before starting the service.'
        logger.error(f'[Kiro] {error_msg}')
        raise ValueError(error_msg)

    # 使用第一个账号
    self.current_account_index = 0
    self._load_creds_from_dict(accounts[0])
    logger.info(f'[Kiro] Using account {0}: {accounts[0].get("description", "N/A")}')

    # 检查是否有refresh_token
    if not self.refresh_token:
        error_msg = 'No refresh token available after loading credentials'
        logger.error(f'[Kiro] {error_msg}')
        raise ValueError(error_msg)

    # 生成机器ID
    self._generate_machine_id()

    await self._ensure_token()

    # 创建 HTTP 会话（优化连接池配置）
    connector = aiohttp.TCPConnector(
        limit=500,  # 增加连接池大小
        limit_per_host=200,  # 增加每个主机的连接数
        force_close=False,
        enable_cleanup_closed=True,  # 启用清理已关闭连接
        ttl_dns_cache=300,  # DNS缓存5分钟
        use_dns_cache=True,  # 启用DNS缓存
    )

    timeout = aiohttp.ClientTimeout(total=300)  # 5分钟超时

    headers = self._build_headers()

    self.session = aiohttp.ClientSession(
        connector=connector,
        headers=headers,
        timeout=timeout
    )

    self.is_initialized = True
    logger.info('[Kiro] Kiro API Service initialized successfully')
```

### 4. FastAPI配置优化

**问题**：默认FastAPI配置可能不适合高并发场景

**解决方案**：优化FastAPI配置

```python
# main.py
import uvicorn

if __name__ == '__main__':
    uvicorn.run(
        'main:app',
        host=settings.HOST,
        port=settings.SERVER_PORT,
        log_level='info',
        workers=4,  # 使用4个工作进程
        limit_concurrency=200,  # 限制并发连接数为200
        timeout_keep_alive=30,  # 保持连接30秒
        access_log=False,  # 禁用访问日志以提高性能
    )
```

### 5. 负载均衡优化

**问题**：单实例处理能力有限

**解决方案**：使用多实例负载均衡

```bash
# 使用generate-loadbalance.sh脚本创建多个实例
./generate-loadbalance.sh 5431 5435  # 创建5个实例，端口5431-5435

# 启动所有实例
docker-compose -f docker-compose-loadbalance.yml up -d

# 配置Nginx负载均衡（已自动生成）
# Nginx将使用轮询策略分发请求到5个实例
```

### 6. 账号池优化

**问题**：单个账号可能达到Kiro API的速率限制

**解决方案**：实现账号池和自动切换

```python
# app/services/kiro_base.py
async def _get_next_account(self) -> Optional[Dict]:
    """获取下一个可用账号（轮询）"""
    if not self.accounts_cache:
        return None

    # 使用轮询策略选择账号
    self.current_account_index = (self.current_account_index + 1) % len(self.accounts_cache)
    return self.accounts_cache[self.current_account_index]

async def _handle_rate_limit(self):
    """处理速率限制"""
    logger.warning('[Kiro] Rate limit reached, switching to next account...')

    # 切换到下一个账号
    switched = await self._switch_to_next_account()
    if switched:
        # 切换账号后刷新令牌
        await self._ensure_token(force_refresh=True)
        return True
    else:
        logger.error('[Kiro] No more accounts available')
        return False
```

### 7. 请求队列优化

**问题**：高并发可能导致请求堆积

**解决方案**：实现请求队列和限流

```python
# app/routes/messages.py
from fastapi import Request
import asyncio

# 请求信号量，限制并发请求数
request_semaphore = asyncio.Semaphore(200)

@router.post(
    '/claude-kiro-oauth/v1/messages',
    response_model=None,
    dependencies=[Depends(verify_authorization)]
)
async def create_message(
    request: Request,
    body: ClaudeMessageRequest
):
    """
    创建消息接口

    功能：
    - 接收 Claude 格式的消息请求
    - 转换为 Kiro API 格式
    - 调用 Kiro API
    - 返回 Claude 格式的响应（支持流式）
    """
    async with request_semaphore:
        controller = get_message_controller()
        return await controller.handle_message(request, body)
```

## 部署建议

### 1. 硬件配置
- **CPU**：至少4核，建议8核以上
- **内存**：至少8GB，建议16GB以上
- **网络**：千兆网络，确保带宽充足

### 2. 实例数量
- 建议至少5个实例，每个实例处理40请求/秒
- 根据实际负载调整实例数量

### 3. 监控
- 监控每个实例的请求处理能力
- 监控错误率和响应时间
- 监控资源使用情况（CPU、内存、网络）

### 4. 扩展策略
- 当负载增加时，增加实例数量
- 当负载减少时，减少实例数量以节省资源

## 性能测试

### 测试工具
使用Apache Bench (ab)或wrk进行性能测试

```bash
# 使用ab测试
ab -n 2000 -c 200 -H "x-api-key: your-api-key" http://localhost:5430/claude-kiro-oauth/v1/messages

# 使用wrk测试
wrk -t4 -c200 -d30s -H "x-api-key: your-api-key" http://localhost:5430/claude-kiro-oauth/v1/messages
```

### 性能指标
- 目标：200请求/秒
- 平均响应时间：< 1秒
- 错误率：< 1%

## 总结

通过以上优化措施，系统应该能够实现每秒处理200次请求的目标。关键优化点包括：

1. API密钥缓存，减少数据库访问
2. 异步日志记录，避免阻塞请求处理
3. 优化连接池配置，提高并发能力
4. 多实例负载均衡，分散请求压力
5. 账号池和自动切换，避免单个账号达到速率限制
6. 请求队列和限流，防止系统过载

这些优化措施可以根据实际情况逐步实施，并根据性能测试结果进行调整。
