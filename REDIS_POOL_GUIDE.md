# Redis 账号池管理方案

## 方案概述

本方案使用 Redis 作为主要存储，SQLite 作为辅助存储，实现高性能的账号池管理。

## 架构设计

### 存储分层

1. **Redis（主存储）**
   - 账号池状态管理
   - 账号使用统计
   - 账号健康状态
   - 账号锁定机制

2. **SQLite（辅助存储）**
   - 账号基本信息
   - 账号配置
   - 仅在初始化和更新时读写

## Redis 数据结构

### 1. 账号池 (Hash)
```
Key: account_pool:{account_id}
Fields:
  - id: 账号ID
  - account: 账号JSON数据
  - status: 状态 (0=禁用, 1=启用, 2=使用中)
  - last_used: 最后使用时间戳
  - usage_count: 使用次数
  - error_count: 错误次数
  - health_score: 健康分数 (0-100)
```

### 2. 可用账号列表 (Sorted Set)
```
Key: available_accounts
Score: 健康分数 + 时间权重
Member: account_id
```

### 3. 账号锁定 (String)
```
Key: account_lock:{account_id}
Value: {timestamp}:{request_id}
TTL: 30秒
```

### 4. 使用统计 (Hash)
```
Key: account_stats:{date}
Fields:
  - total_requests: 总请求数
  - success_count: 成功数
  - error_count: 错误数
  - avg_response_time: 平均响应时间
```

## 核心操作流程

### 1. 获取可用账号

```python
async def get_available_account():
    # 从可用账号列表中获取健康分数最高的账号
    account_id = redis.zrevrange('available_accounts', 0, 0)
    if not account_id:
        return None

    # 检查账号是否被锁定
    lock_key = f"account_lock:{account_id}"
    if redis.exists(lock_key):
        # 获取下一个可用账号
        return get_next_available_account()

    # 锁定账号
    redis.setex(lock_key, 30, f"{time.time()}:{request_id}")

    # 获取账号详情
    account_data = redis.hgetall(f"account_pool:{account_id}")

    # 更新状态为使用中
    redis.hset(f"account_pool:{account_id}", "status", 2)

    return account_data
```

### 2. 释放账号

```python
async def release_account(account_id, success=True, response_time=0):
    # 释放账号锁
    redis.delete(f"account_lock:{account_id}")

    # 更新使用统计
    account_key = f"account_pool:{account_id}"
    redis.hincrby(account_key, "usage_count", 1)
    redis.hset(account_key, "last_used", time.time())

    if success:
        # 成功：提高健康分数
        redis.hincrby(account_key, "health_score", 5)
        redis.hset(account_key, "error_count", 0)
    else:
        # 失败：降低健康分数
        redis.hincrby(account_key, "error_count", 1)
        redis.hincrby(account_key, "health_score", -10)

    # 限制健康分数范围
    health_score = max(0, min(100, redis.hget(account_key, "health_score")))
    redis.hset(account_key, "health_score", health_score)

    # 更新可用账号列表
    if health_score > 30:  # 只将健康账号放回池中
        redis.zadd('available_accounts', {account_id: health_score})

    # 更新状态为启用
    redis.hset(account_key, "status", 1)
```

### 3. 初始化账号池

```python
async def initialize_pool():
    # 从 SQLite 读取账号列表
    accounts = db.query(Account).filter(Account.status == '1').all()

    # 批量加载到 Redis
    pipe = redis.pipeline()
    for account in accounts:
        account_key = f"account_pool:{account.id}"
        pipe.hset(account_key, {
            'id': account.id,
            'account': account.account,
            'status': 1,
            'last_used': 0,
            'usage_count': 0,
            'error_count': 0,
            'health_score': 100
        })
        pipe.zadd('available_accounts', {account.id: 100})

    pipe.execute()
    logger.info(f"Initialized pool with {len(accounts)} accounts")
```

### 4. 更新账号

```python
async def update_account(account_id, account_data):
    # 更新 SQLite
    db.query(Account).filter(Account.id == account_id).update(account_data)
    db.commit()

    # 更新 Redis
    account_key = f"account_pool:{account_id}"
    redis.hset(account_key, 'account', json.dumps(account_data))

    # 如果状态变化，更新可用列表
    if 'status' in account_data:
        if account_data['status'] == '1':
            redis.zadd('available_accounts', {account_id: 100})
        else:
            redis.zrem('available_accounts', account_id)
```

## 性能优化

### 1. 批量操作
- 使用 Redis Pipeline 批量执行命令
- 减少网络往返次数

### 2. 连接池
```python
redis_pool = redis.ConnectionPool(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    max_connections=50,
    decode_responses=True
)
redis = redis.Redis(connection_pool=redis_pool)
```

### 3. Lua 脚本
对于复杂操作，使用 Lua 脚本保证原子性：

```lua
-- 获取并锁定账号
local account_id = redis.call('ZREVRANGE', 'available_accounts', 0, 0)
if not account_id or #account_id == 0 then
    return nil
end

local lock_key = 'account_lock:' .. account_id
if redis.call('EXISTS', lock_key) == 1 then
    return nil
end

redis.call('SETEX', lock_key, 30, ARGV[1])
redis.call('HSET', 'account_pool:' .. account_id, 'status', 2)
return redis.call('HGETALL', 'account_pool:' .. account_id)
```

## 容错机制

### 1. 账号健康检查
- 定期检查账号健康状态
- 自动禁用不健康的账号
- 自动恢复健康的账号

### 2. 故障转移
- Redis 不可用时降级到 SQLite
- 自动重试机制
- 请求队列缓冲

### 3. 数据同步
- 定期同步 Redis 和 SQLite 数据
- 确保 SQLite 作为真实数据源
- Redis 作为缓存层

## 监控指标

### 1. 池状态
- 总账号数
- 可用账号数
- 使用中账号数
- 平均健康分数

### 2. 性能指标
- 请求成功率
- 平均响应时间
- 账号轮换频率
- 错误率

## 配置示例

```python
# config.py
REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
REDIS_DB = int(os.getenv('REDIS_DB', 0))
REDIS_PASSWORD = os.getenv('REDIS_PASSWORD', None)

POOL_SIZE = int(os.getenv('POOL_SIZE', 10))
ACCOUNT_TIMEOUT = int(os.getenv('ACCOUNT_TIMEOUT', 30))
HEALTH_CHECK_INTERVAL = int(os.getenv('HEALTH_CHECK_INTERVAL', 300))
```

## 部署建议

### 1. Redis 配置
```conf
# redis.conf
maxmemory 256mb
maxmemory-policy allkeys-lru
save 900 1
save 300 10
save 60 10000
```

### 2. Docker Compose
```yaml
version: '3'
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes
    restart: unless-stopped

volumes:
  redis_data:
```

## 使用示例

### 1. 启动服务
```bash
# 启动 Redis
docker-compose up -d redis

# 初始化账号池
python -c "from app.services.account_pool import initialize_pool; import asyncio; asyncio.run(initialize_pool())"

# 启动应用
python main.py
```

### 2. 监控池状态
```bash
# 查看可用账号数
redis-cli ZCARD available_accounts

# 查看账号详情
redis-cli HGETALL account_pool:1

# 查看健康分数
redis-cli ZREVRANGE available_accounts 0 -1 WITHSCORES
```

## 注意事项

1. **数据一致性**
   - SQLite 是真实数据源
   - Redis 作为缓存层
   - 定期同步确保一致性

2. **性能平衡**
   - Redis 读写速度快
   - SQLite 读写频率低
   - 合理设置同步间隔

3. **故障恢复**
   - Redis 重启后从 SQLite 重建池
   - 自动故障转移机制
   - 监控告警机制

4. **扩展性**
   - 支持水平扩展
   - 多实例共享账号池
   - 负载均衡策略
