
# 配置文件
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """应用配置类"""

    # 服务器配置
    HOST: str = "0.0.0.0"
    SERVER_PORT: int = 5431
    REQUIRED_API_KEY: str = "123456"

    # 代理配置
    PROXY_SERVER: Optional[str] = None
    USE_SYSTEM_PROXY_KIRO: bool = False
    PROXY_VALIDATE_ON_LOAD: bool = True  # 是否在加载代理时验证连接
    PROXY_VALIDATE_TIMEOUT: int = 5  # 代理验证超时时间（秒）
    PROXY_DISABLE_SSL: bool = True  # 禁用代理SSL验证以避免TLS in TLS问题
    # 超时配置 - 优化为更合理的值，适应第三方AI响应慢的情况
    PROXY_CONNECT_TIMEOUT: int = 30  # 代理连接超时时间（秒）
    PROXY_READ_TIMEOUT: int = 300  # 代理读取超时时间（秒）- 适应AI响应慢的情况
    PROXY_TOTAL_TIMEOUT: int = 300  # 代理总超时时间（秒）- 适应AI响应慢的情况

    # 流式请求超时配置 - 流式请求需要更长的超时时间
    STREAM_CONNECT_TIMEOUT: int = 30  # 流式连接超时时间（秒）
    STREAM_READ_TIMEOUT: int = 300  # 流式读取超时时间（秒）- 适应AI响应慢的情况
    STREAM_TOTAL_TIMEOUT: int = 300  # 流式总超时时间（秒）- 适应AI响应慢的情况

    # 非流式请求超时配置 - 非流式请求可以设置较短的超时时间
    NONSTREAM_CONNECT_TIMEOUT: int = 20  # 非流式连接超时时间（秒）
    NONSTREAM_READ_TIMEOUT: int = 180  # 非流式读取超时时间（秒）- 适应AI响应慢的情况
    NONSTREAM_TOTAL_TIMEOUT: int = 180  # 非流式总超时时间（秒）- 适应AI响应慢的情况

    # 队列管理器超时配置
    QUEUE_MANAGER_TIMEOUT: int = 1200  # 队列管理器超时时间（秒）- 增加到1200秒
    QUEUE_MANAGER_MAX_WORKERS: int = 1000  # Worker数量
    QUEUE_MANAGER_QUEUE_SIZE: int = 20000  # 队列容量

    # 连接池配置 - 优化为适应AI响应慢的情况
    KIRO_SESSION_POOL_SIZE: int = 500  # Session连接池大小 - 减少资源占用
    KIRO_SESSION_LIMIT_PER_HOST: int = 100  # 每个主机最大连接数 - 减少资源竞争
    KIRO_SESSION_KEEPALIVE_TIMEOUT: int = 120  # 保持连接时间（秒）- 增加以适应慢响应

    # Redis 配置
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: Optional[str] = None
    REDIS_MAX_CONNECTIONS: int = 200

    # PostgreSQL 配置
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "1234"
    POSTGRES_DB: str = "postgres"
    POSTGRES_POOL_SIZE: int = 200
    POSTGRES_MAX_OVERFLOW: int = 200
    POSTGRES_POOL_RECYCLE: int = 1800
    POSTGRES_POOL_TIMEOUT: int = 30

    # 账号池配置
    POOL_SIZE: int = 1
    ACCOUNT_TIMEOUT: int = 30
    HEALTH_CHECK_INTERVAL: int = 300

    # Kiro OAuth 配置
    KIRO_OAUTH_CREDS_BASE64: Optional[str] = None
    KIRO_OAUTH_CREDS_FILE_PATH: Optional[str] = None
    KIRO_REFRESH_URL: str = "https://prod.{{region}}.auth.desktop.kiro.dev/refreshToken"
    KIRO_REFRESH_IDC_URL: str = "https://oidc.{{region}}.amazonaws.com/token"
    KIRO_BASE_URL: str = "https://codewhisperer.{{region}}.amazonaws.com/generateAssistantResponse"
    KIRO_AMAZON_Q_URL: str = "https://codewhisperer.{{region}}.amazonaws.com/SendMessageStreaming"
    KIRO_USAGE_LIMITS_URL: str = "https://q.{{region}}.amazonaws.com/getUsageLimits"

    # 请求配置 - 优化为适应AI响应慢的情况
    REQUEST_MAX_RETRIES: int = 3  # 增加重试次数，从1提高到3
    REQUEST_BASE_DELAY: int = 1000  # 基础延迟（毫秒）- 从200提高到1000
    REQUEST_MAX_DELAY: int = 10000  # 最大重试延迟（毫秒）- 新增配置
    REQUEST_TIMEOUT: int = 120  # 请求总超时时间（秒）
    REQUEST_CONNECT_TIMEOUT: int = 10  # 连接超时时间（秒）- 从15降低到10
    REQUEST_READ_TIMEOUT: int = 100  # 读取超时时间（秒）

    # 日志配置
    PROMPT_LOG_MODE: str = "none"
    PROMPT_LOG_BASE_NAME: str = "prompt_log"

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
