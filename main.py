
# 应用入口
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routes.messages import router as messages_router
from app.services.kiro_service import get_kiro_service

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时初始化服务
    logger.info('Starting application...')
    kiro_service = get_kiro_service()
    await kiro_service.initialize()
    logger.info('Kiro service initialized')

    yield

    # 关闭时清理资源
    logger.info('Shutting down application...')
    await kiro_service.close()
    logger.info('Application shutdown complete')


# 创建 FastAPI 应用
app = FastAPI(
    title='Kiro API FastAPI',
    description='Claude Kiro OAuth API - FastAPI 实现',
    version='1.0.0',
    lifespan=lifespan
)

# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
    allow_headers=['Content-Type', 'Authorization', 'x-api-key', 'Model-Provider']
)


# 注册路由
app.include_router(messages_router)


@app.get('/health')
async def health_check():
    """健康检查接口"""
    return {
        'status': 'healthy',
        'timestamp': settings.SERVER_PORT,
        'provider': 'claude-kiro-oauth'
    }


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(
        'main:app',
        host=settings.HOST,
        port=settings.SERVER_PORT,
        log_level='info'
    )
