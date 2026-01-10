# Kiro 会话接口使用说明

## 概述

新的会话接口 (`messages_sessions.py`) 提供了与原接口 (`messages.py`) 相同的功能，但支持会话模式，可以保持多轮对话的上下文。

## 主要特性

1. **会话管理**
   - 自动创建和管理会话
   - 支持会话复用
   - 自动清理过期会话

2. **高并发支持**
   - 不使用实例池模式
   - 每个请求独立处理
   - 支持每秒 200 并发请求

3. **错误处理**
   - 自动处理 403 错误（账号禁用）
   - 自动处理 429 错误（限流）
   - 自动释放账号和代理资源

4. **兼容性**
   - 接口路径与原接口相同（在路由前缀后）
   - 请求格式完全兼容
   - 响应格式完全兼容

## 接口路径

### 主接口
```
POST /claude-kiro-oauth/v1/messages
```

### 辅助接口
```
GET  /queue/health
POST /sessions/{session_id}/close
GET  /sessions/{session_id}
```

## 使用方式

### 1. 普通请求（无会话）

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: your-api-key" \
  -d '{
    "model": "claude-sonnet-4-5",
    "messages": [
      {
        "role": "user",
        "content": "你好"
      }
    ],
    "stream": false
  }' \
  http://localhost:8000/claude-kiro-oauth/v1/messages
```

### 2. 会话请求（带 session_id）

```bash
# 第一次请求（创建会话）
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: your-api-key" \
  -d '{
    "model": "claude-sonnet-4-5",
    "messages": [
      {
        "role": "user",
        "content": "你好"
      }
    ],
    "stream": false,
    "metadata": {
      "session_id": "session_abc123"
    }
  }' \
  http://localhost:8000/claude-kiro-oauth/v1/messages
```

响应头会包含 `X-Session-Id`：
```
X-Session-Id: session_abc123
```

### 3. 多轮对话（复用会话）

```bash
# 第二次请求（使用相同 session_id）
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: your-api-key" \
  -d '{
    "model": "claude-sonnet-4-5",
    "messages": [
      {
        "role": "user",
        "content": "你好"
      },
      {
        "role": "assistant",
        "content": "你好！有什么可以帮助你的吗？"
      },
      {
        "role": "user",
        "content": "请介绍一下Python"
      }
    ],
    "stream": false,
    "metadata": {
      "session_id": "session_abc123"
    }
  }' \
  http://localhost:8000/claude-kiro-oauth/v1/messages
```

### 4. 流式请求

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: your-api-key" \
  -d '{
    "model": "claude-sonnet-4-5",
    "messages": [
      {
        "role": "user",
        "content": "你好"
      }
    ],
    "stream": true
  }' \
  http://localhost:8000/claude-kiro-oauth/v1/messages
```

### 5. 关闭会话

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: your-api-key" \
  http://localhost:8000/sessions/session_abc123/close
```

### 6. 获取会话信息

```bash
curl -X GET \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: your-api-key" \
  http://localhost:8000/sessions/session_abc123
```

### 7. 获取队列健康状态

```bash
curl -X GET \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: your-api-key" \
  http://localhost:8000/queue/health
```

## 会话管理

### 会话生命周期

1. **创建会话**
   - 首次请求时自动创建
   - 生成唯一的 session_id
   - 创建 conversation_id

2. **使用会话**
   - 后续请求使用相同 session_id
   - 保持对话历史
   - 复用 conversation_id

3. **会话过期**
   - 默认 30 分钟未使用自动过期
   - 自动清理过期会话
   - 释放相关资源

### 会话限制

- 最大会话数：10000
- 会话超时：30 分钟
- 支持并发：200 QPS

## 错误处理

### 403 错误（账号禁用）

```json
{
  "type": "error",
  "error": {
    "type": "forbidden",
    "message": "账号已被禁用"
  }
}
```

### 429 错误（限流）

```json
{
  "type": "error",
  "error": {
    "type": "rate_limit",
    "message": "请求过于频繁"
  }
}
```

### 503 错误（服务不可用）

```json
{
  "type": "error",
  "error": {
    "type": "service_unavailable",
    "message": "服务暂不可用：没有可用的Kiro账号"
  }
}
```

## 与原接口的差异

| 特性 | 原接口 | 会话接口 |
|------|--------|----------|
| 会话支持 | ❌ | ✅ |
| 并发模型 | 实例池 | 无实例化 |
| 最大并发 | 受限 | 200 QPS |
| 上下文保持 | ❌ | ✅ |
| 接口路径 | /claude-kiro-oauth/v1/messages | /claude-kiro-oauth/v1/messages |
| 请求格式 | Claude | Claude |
| 响应格式 | Claude | Claude |

## 集成到主应用

在主应用的路由中注册会话路由：

```python
from app.routes.messages_sessions import router as messages_sessions_router

app.include_router(
    messages_sessions_router,
    prefix="/api/v1",
    tags=["messages-sessions"]
)
```

## 注意事项

1. **API Key 验证**
   - 所有接口都需要有效的 API Key
   - API Key 通过 `X-Api-Key` 头传递

2. **会话 ID**
   - 首次请求可以不提供 session_id
   - 后续请求应该提供 session_id 以保持上下文
   - session_id 在响应头中返回

3. **资源管理**
   - 账号和代理自动释放
   - 过期会话自动清理
   - 无需手动管理资源

4. **错误重试**
   - 403 错误会自动切换账号
   - 429 错误会自动重试
   - 其他错误返回给客户端处理

## 性能优化

1. **高并发**
   - 无实例池开销
   - 直接创建服务实例
   - 快速响应请求

2. **资源复用**
   - 会话历史缓存
   - 避免重复发送历史
   - 减少 token 消耗

3. **自动清理**
   - 定期清理过期会话
   - 释放内存资源
   - 保持系统稳定
