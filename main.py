# 应用入口
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routes.messages import router as messages_router
from app.services.kiro_service import get_kiro_service
from app.db.database import init_db
from app.db.init_data import init_default_user
from app.api.management import router as management_router
from app.api.pool import router as pool_router
from app.services.heartbeat import heartbeat_service
from app.services.account_pool import initialize_pool, close_redis
from app.services.proxy_pool import initialize_pool as initialize_proxy_pool, close_redis as close_proxy_redis
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

    # 初始化数据库
    from app.db.database import init_db
    await init_db()
    logger.info('Database initialized')

    # 初始化默认管理员用户
    await init_default_user()
    logger.info('Default admin user initialized')
    
    # 初始化账号池
    await initialize_pool()
    logger.info('Account pool initialized')

    # 初始化代理池
    await initialize_proxy_pool()
    logger.info('Proxy pool initialized')
    # 初始化并启动心跳服务
    heartbeat_service.init_app(app)
    heartbeat_service.start()
    logger.info('Heartbeat service started')

    # 注意：Kiro服务将在首次使用时初始化，而不是在启动时
    # 这样可以避免因为没有可用账号而导致服务无法启动

    yield

    # 关闭时清理资源
    logger.info('Shutting down application...')
    # 停止心跳服务
    heartbeat_service.stop()
    # 关闭请求队列管理器
    from app.routes.message_queue import get_queue_manager
    queue_manager = get_queue_manager()
    await queue_manager.shutdown()
    # 关闭数据库引擎
    from app.db.database import engine
    await engine.dispose()
    # 尝试关闭Kiro服务（如果已初始化）
    kiro_service = get_kiro_service()
    if kiro_service.is_initialized:
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

# 挂载静态文件目录
app.mount("/static", StaticFiles(directory="app/static"), name="static")


# 注册路由
app.include_router(messages_router)
app.include_router(management_router, prefix="/api/management", tags=["管理"])
app.include_router(pool_router, prefix="/api/pool", tags=["账号池"])
@app.get('/')
async def root():
    """根路径自动跳转到登录页面"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url='/static/login.html')


@app.get('/health')
async def health_check():
    """健康检查接口"""
    kiro_service = get_kiro_service()
    return {
        'status': 'healthy',
        'timestamp': settings.SERVER_PORT,
        'provider': 'claude-kiro-oauth',
        'kiro_service': {
            'initialized': kiro_service.is_initialized,
            'has_accounts': len(kiro_service.accounts_cache) > 0 if kiro_service.accounts_cache else False,
            'accounts_count': len(kiro_service.accounts_cache) if kiro_service.accounts_cache else 0
        }
    }
@app.get('/connection-pool-status')
async def connection_pool_status():
    """连接池状态监控接口"""
    kiro_service = get_kiro_service()

    if not kiro_service.is_initialized or not kiro_service.session:
        return {
            'status': 'not_initialized',
            'message': 'Kiro service not initialized'
        }

    connector = kiro_service.session.connector
    return {
        'status': 'active',
        'connection_pool': {
            'total_limit': connector.limit,
            'limit_per_host': connector.limit_per_host,
            'total_connections': len(connector._conns),
            'active_connections': sum(len(conns) for conns in connector._conns.values()),
            'connections_by_host': {
                host: len(conns) 
                for host, conns in connector._conns.items()
            }
        },
        'proxy': {
            'enabled': kiro_service.proxy is not None,
            'proxy_url': kiro_service.proxy if kiro_service.proxy else None
        }
    }


if __name__ == '__main__':
    import uvicorn
    import os

    # 尝试使用 uvloop 和 httptools 提升性能
    try:
        import uvloop
        uvloop.install()
        logger.info('uvloop installed and enabled')
    except ImportError:
        logger.info('uvloop not available, using default event loop')

    # 配置 uvicorn
    uvicorn.run(
        'main:app',
        host=settings.HOST,
        port=settings.SERVER_PORT,
        log_level='info',
        loop='uvloop' if os.name != 'nt' else None,  # Windows 不支持 uvloop
        access_log=True,
        use_colors=True,
        limit_concurrency=1000,
        timeout_keep_alive=5
    )
