
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

    # Kiro OAuth 配置
    KIRO_OAUTH_CREDS_BASE64: Optional[str] = None
    KIRO_OAUTH_CREDS_FILE_PATH: Optional[str] = None
    KIRO_REFRESH_URL: str = "https://prod.{{region}}.auth.desktop.kiro.dev/refreshToken"
    KIRO_REFRESH_IDC_URL: str = "https://oidc.{{region}}.amazonaws.com/token"
    KIRO_BASE_URL: str = "https://codewhisperer.{{region}}.amazonaws.com/generateAssistantResponse"
    KIRO_AMAZON_Q_URL: str = "https://codewhisperer.{{region}}.amazonaws.com/SendMessageStreaming"
    KIRO_USAGE_LIMITS_URL: str = "https://q.{{region}}.amazonaws.com/getUsageLimits"

    # 请求配置
    REQUEST_MAX_RETRIES: int = 3
    REQUEST_BASE_DELAY: int = 1000

    # 日志配置
    PROMPT_LOG_MODE: str = "none"
    PROMPT_LOG_BASE_NAME: str = "prompt_log"

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
