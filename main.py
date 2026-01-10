import warnings
warnings.filterwarnings('ignore', category=UserWarning, message='Invalid HTTP request received')
# 应用入口
import logging
import signal
import sys
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routes.messages import router as messages_router
from app.routes.messages_sessions import router as messages_sessions_router
from app.services.kiro_service import get_kiro_service
from app.db.database import init_db
from app.db.init_data import init_default_user
from app.api.management import router as management_router
from app.api.pool import router as pool_router
from app.api.monitoring import router as monitoring_router
from app.services.heartbeat import heartbeat_service
from app.services.account_pool_v2 import initialize_pool, close_redis, stop_reloader, AccountPoolReloader, account_pool_reloader
from app.services.proxy_pool import initialize_pool as initialize_proxy_pool, close_redis as close_proxy_redis
from app.services.proxy_health_checker import ProxyHealthChecker
from app.services.apikey_manager import init_redis as init_apikey_redis, close_redis as close_apikey_redis, load_apikeys_to_redis

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

logging.getLogger('aiohttp').setLevel(logging.ERROR)
logging.getLogger('aiohttp.access').setLevel(logging.ERROR)
logging.getLogger("uvicorn.protocols.http.h11_impl").setLevel(logging.ERROR)
logging.getLogger("uvicorn.access").setLevel(logging.ERROR)
logging.getLogger("uvicorn.server").setLevel(logging.WARNING)

# ✅ 新增3行：全覆盖协议层日志器，彻底封杀Invalid HTTP警告
logging.getLogger("uvicorn.protocols.http").setLevel(logging.CRITICAL)
logging.getLogger("uvicorn.protocols").setLevel(logging.CRITICAL)
logging.getLogger("h11").setLevel(logging.CRITICAL)
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global account_pool_reloader, proxy_health_checker
    # 启动时初始化服务
    logger.info('Starting application...')

    # 初始化数据库
    from app.db.database import init_db
    await init_db()
    logger.info('数据库初始化成功')

    # 初始化默认管理员用户
    await init_default_user()
    logger.info('Default admin user initialized')

    # 只在主worker中初始化共享资源
    is_main_worker = os.environ.get('WORKER_ID') is None or os.environ.get('WORKER_ID') == '0'
    
    if is_main_worker:
        logger.info('Main worker detected, initializing shared resources...')
        
        # 初始化账号池
        await initialize_pool()
        logger.info('Account pool initialized')

        # 初始化API Key管理服务
        init_apikey_redis()
        await load_apikeys_to_redis()
        logger.info('API Key manager initialized')

        # 启动账号重载器
        if account_pool_reloader is None:
            account_pool_reloader = AccountPoolReloader(reload_interval=60)
            await account_pool_reloader.start()
            logger.info('Account pool reloader started')
        

        # 初始化代理池
        await initialize_proxy_pool()
        logger.info('Proxy pool initialized')

        # 初始化代理健康检查服务
        proxy_health_checker = ProxyHealthChecker(check_interval=30)
        await proxy_health_checker.start_health_check_loop()
        logger.info('Proxy health checker started')

        # 初始化Kiro服务池
        from app.services.kiro_service_new import get_kiro_service
        kiro_pool = get_kiro_service()
        if not kiro_pool.services:
            await kiro_pool.initialize()
        logger.info(f'Kiro service pool initialized with {len(kiro_pool.services)} services')

        # 初始化并启动心跳服务
        heartbeat_service.init_app(app)
        heartbeat_service.start()
        logger.info('Heartbeat service started')
    else:
        logger.info(f'Worker {os.environ.get("WORKER_ID")} detected, skipping shared resource initialization')

    yield

    # 关闭时清理资源
    logger.info('Shutting down application...')
    
    # 只在主worker中清理共享资源
    if is_main_worker:
        logger.info('Main worker detected, cleaning up shared resources...')
        # 停止账号重载器
        await stop_reloader()
        # 停止心跳服务
        heartbeat_service.stop()
        
        # 关闭请求队列管理器
        from app.routes.redis_queue_manager import get_queue_manager
        queue_manager = get_queue_manager()
        await queue_manager.shutdown()
        
        # 关闭数据库引擎
        from app.db.database import engine
        await engine.dispose()
        
        # 关闭 Redis 连接
        close_redis()
        close_proxy_redis()
        close_apikey_redis()
        
        # 关闭Kiro服务池
        from app.services.kiro_service_new import get_kiro_service as get_kiro_pool
        kiro_pool = get_kiro_pool()
        if kiro_pool.services:
            await kiro_pool.close_all()
            logger.info('Kiro service pool closed')

        # 关闭Kiro服务单例（kiro_service.py中的单例）
        from app.services.kiro_service import get_kiro_service
        kiro_service = get_kiro_service()
        if kiro_service.session is not None:
            await kiro_service.close()
            logger.info('Kiro service singleton closed')

        # 停止代理健康检查服务
        await proxy_health_checker.stop_health_check()
        logger.info('Proxy health checker stopped')

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
app.include_router(messages_sessions_router, prefix="/api")
app.include_router(management_router, prefix="/api/management", tags=["管理"])
app.include_router(pool_router, prefix="/api/pool", tags=["账号池"])
app.include_router(monitoring_router, prefix="/api/monitoring", tags=["监控"])


@app.get('/')
async def root():
    """根路径自动跳转到登录页面"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url='/static/login.html')


@app.get('/health')
async def health_check():
    """健康检查接口"""
    # 获取服务池统计信息
    from app.services.kiro_service_new import get_kiro_service as get_kiro_pool
    kiro_pool = get_kiro_pool()

    # 获取服务实例
    kiro_service = get_kiro_service()

    # 获取账号池统计信息
    from app.services.account_pool_v2 import get_pool_stats
    pool_stats = await get_pool_stats()

    # 获取代理池统计信息
    from app.services.proxy_pool import get_proxy_pool_stats
    proxy_stats = await get_proxy_pool_stats()

    return {
        'status': 'healthy',
        'timestamp': settings.SERVER_PORT,
        'provider': 'claude-kiro-oauth',
        'kiro_service': {
            'initialized': kiro_service.is_initialized,
            'has_accounts': len(kiro_service.accounts_cache) > 0 if kiro_service.accounts_cache else False,
            'accounts_count': len(kiro_service.accounts_cache) if kiro_service.accounts_cache else 0
        },
        'service_pool': {
            'pool_size': kiro_pool.pool_size,
            'active_services': len(kiro_pool.services),
            'current_index': kiro_pool.current_index
        },
        'account_pool': pool_stats,
        'proxy_pool': proxy_stats
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
    import multiprocessing

    # 从环境变量获取配置，如果没有则使用默认值
    workers = int(os.getenv('UVICORN_WORKERS', str(multiprocessing.cpu_count() * 1)))
    host = os.getenv('UVICORN_HOST', settings.HOST)
    port = int(os.getenv('UVICORN_PORT', str(settings.SERVER_PORT)))
    limit_concurrency = int(os.getenv('UVICORN_LIMIT_CONCURRENCY', '200'))
    timeout_keep_alive = int(os.getenv('UVICORN_TIMEOUT_KEEP_ALIVE', '300'))
    backlog = int(os.getenv('UVICORN_BACKLOG', '2048'))

    logger.info(f'Starting uvicorn with config: workers={workers}, host={host}, port={port}, '
                f'limit_concurrency={limit_concurrency}, timeout_keep_alive={timeout_keep_alive}, backlog={backlog}')

    # 创建配置
    config = uvicorn.Config(
        'main:app',
        host=host,
        port=port,
        workers=workers,              # 从环境变量或默认值获取
        log_level="info",        
        limit_concurrency=limit_concurrency,       # 从环境变量或默认值获取
        timeout_keep_alive=timeout_keep_alive,        # 从环境变量或默认值获取
        backlog=backlog,                # 从环境变量或默认值获取
        timeout_graceful_shutdown=30,  # 优雅关闭超时
        limit_max_requests=10000,     # 每个工作进程最大请求数后重启
        access_log=False               # 禁用访问日志减少IO
    )

    # 创建服务器实例
    server = uvicorn.Server(config)

    # 设置信号处理器
    def handle_signal(signum, frame):
        logger.info(f'Received signal {signum}, shutting down...')
        server.should_exit = True

    # 注册信号处理器
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Windows下也支持SIGBREAK
    if sys.platform == 'win32':
        signal.signal(signal.SIGBREAK, handle_signal)

    # 运行服务器
    server.run()
