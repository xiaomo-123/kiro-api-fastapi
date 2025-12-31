
# Kiro API FastAPI

基于 Node.js 项目 [AIClient-2-API](../AIClient-2-API) 的 `/claude-kiro-oauth/v1/messages` 接口，使用 Python FastAPI 实现的完整功能版本。

## 功能特性

- 功能100%一致：接口的入参校验、逻辑处理、出参格式、异常捕获、第三方依赖调用与原接口无任何功能偏差
- 数据零丢失：请求/响应的所有字段（包括可选字段、嵌套字段、特殊格式字段如JSON/Base64）均完整保留
- 代码简洁无冗余：仅保留接口运行必需的逻辑
- 代码结构分明：按「路由层-控制器层-服务层-工具层」分层设计

## 项目结构

```
kiro-api-fastapi/
├── app/
│   ├── __init__.py
│   ├── config.py              # 配置管理
│   ├── models.py              # 数据模型（Pydantic）
│   ├── utils.py              # 工具函数
│   ├── services/
│   │   ├── __init__.py
│   │   └── kiro_service.py  # Kiro API服务层
│   ├── controllers/
│   │   ├── __init__.py
│   │   └── message_controller.py  # 消息控制器
│   └── routes/
│       ├── __init__.py
│       └── messages.py       # 路由层
├── .env.example             # 环境变量示例
├── requirements.txt          # 依赖列表
├── main.py                 # 应用入口
└── README.md               # 项目文档
```

## 环境配置

复制 `.env.example` 为 `.env` 并配置以下变量：

```env
# 服务器配置
HOST=0.0.0.0
SERVER_PORT=5431
REQUIRED_API_KEY=123456

# 代理配置
PROXY_SERVER=
USE_SYSTEM_PROXY_KIRO=false

# Redis 配置
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=
REDIS_MAX_CONNECTIONS=50

# PostgreSQL 配置
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_USER=postgres
POSTGRES_PASSWORD=1234
POSTGRES_DB=postgres
POSTGRES_POOL_SIZE=20
POSTGRES_MAX_OVERFLOW=10
POSTGRES_POOL_RECYCLE=3600

# 账号池配置
POOL_SIZE=10
ACCOUNT_TIMEOUT=30
HEALTH_CHECK_INTERVAL=300

# Kiro OAuth配置（二选一）
KIRO_OAUTH_CREDS_BASE64=your_base64_encoded_credentials
# 或
KIRO_OAUTH_CREDS_FILE_PATH=/path/to/credentials.json

# 请求配置
REQUEST_MAX_RETRIES=3
REQUEST_BASE_DELAY=1000

# 日志配置
PROMPT_LOG_MODE=none
PROMPT_LOG_BASE_NAME=prompt_log
```

### 数据库说明

本项目使用PostgreSQL作为默认数据库，支持高并发场景。

#### 使用Docker Compose启动

```bash
docker-compose up -d
```

这将自动启动以下服务：
- PostgreSQL数据库（端口5432）
- Redis缓存（端口6379）
- API应用（端口5431）

#### 手动启动PostgreSQL

Windows:
```bash
start_postgres.bat
```

Linux/Mac:
```bash
chmod +x start_postgres.sh
./start_postgres.sh
```

#### 连接到PostgreSQL

```bash
docker exec -it kiro-postgres psql -U postgres
```

详细的PostgreSQL配置说明请参考 [POSTGRES_GUIDE.md](POSTGRES_GUIDE.md)

## 安装与运行

### 安装依赖

```bash
pip install -r requirements.txt
```

### 启动服务

```bash
python main.py
```

或使用 uvicorn：

```bash
uvicorn main:app --host 0.0.0.0 --port 5431
```

## 接口测试

### 非流式请求

```bash
curl -X POST http://localhost:5431/claude-kiro-oauth/v1/messages   -H "Content-Type: application/json"   -H "Authorization: Bearer your_api_key"   -d '{
    "model": "claude-sonnet-4-5",
    "max_tokens": 4096,
    "messages": [
      {"role": "user", "content": "Hello, how are you?"}
    ]
  }'
```

### 流式请求

```bash
curl -X POST http://localhost:5431/claude-kiro-oauth/v1/messages   -H "Content-Type: application/json"   -H "Authorization: Bearer your_api_key"   -d '{
    "model": "claude-sonnet-4-5",
    "max_tokens": 4096,
    "stream": true,
    "messages": [
      {"role": "user", "content": "Hello, how are you?"}
    ]
  }'
```

### 健康检查

```bash
curl http://localhost:5431/health
```

## API 文档

启动服务后访问：
- Swagger UI: http://localhost:3001/docs
- ReDoc: http://localhost:5431/redoc

## 一致性校验清单

### 入参校验规则
- [x] 使用Pydantic进行参数验证
- [x] 支持messages数组格式（包括text、image、tool_result、tool_use等类型）
- [x] 支持system字段（字符串或对象）
- [x] 支持tools字段（工具定义数组）
- [x] 支持stream标志（流式/非流式响应）

### 认证逻辑
- [x] 支持Bearer Token认证
- [x] 支持API Key在URL参数中
- [x] 支持x-api-key头
- [x] 令牌过期检查和自动刷新
- [x] 403错误时自动重试

### API调用
- [x] 使用相同的请求头（User-Agent、x-amzn-kiro-agent-mode等）
- [x] 支持代理配置
- [x] 实现指数退避重试机制（429、5xx错误）
- [x] 请求超时设置为5分钟

### 响应格式
- [x] 非流式响应：完整Claude格式（id、type、role、content、model、usage等）
- [x] 流式响应：SSE格式（message_start、content_block_delta、message_stop等事件）
- [x] 工具调用：支持结构化工具调用和括号格式解析
- [x] Token计数：使用字符数估算

### 错误处理
- [x] 401未授权：返回标准错误格式
- [x] 403权限错误：自动刷新令牌并重试
- [x] 429限流：指数退避重试
- [x] 5xx服务器错误：重试机制
- [x] 网络异常：捕获并返回友好错误信息

### 数据完整性
- [x] 保留所有请求字段（包括可选字段）
- [x] 保留所有响应字段
- [x] 支持Base64编码的图片数据
- [x] 支持嵌套的JSON结构

## 技术栈

- **框架**: FastAPI 0.104.1
- **ASGI服务器**: Uvicorn 0.24.0
- **数据验证**: Pydantic 2.5.0
- **HTTP客户端**: aiohttp 3.9.1
- **配置管理**: pydantic-settings 2.1.0

## 注意事项

1. 首次运行前必须配置 `.env` 文件
2. Kiro OAuth凭证需要通过Base64编码或JSON文件路径提供
3. 代理配置可选，根据网络环境决定是否启用
4. 流式响应使用SSE格式，客户端需支持EventSource解析
5. Token计数使用字符数估算，非精确计数

## 许可证

本项目基于原 Node.js 项目实现，遵循相同的许可证。
