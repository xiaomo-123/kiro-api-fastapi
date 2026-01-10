# 监控接口
from fastapi import APIRouter
from ..db.database import engine
from ..config import settings
from ..services.kiro_service import get_kiro_service

router = APIRouter()

@router.get('/db-pool-status')
async def db_pool_status():
    """数据库连接池状态监控接口"""
    # 获取数据库连接池状态
    pool = engine.pool
    return {
        'status': 'active',
        'pool_size': pool.size(),
        'checked_in': pool.checkedin(),
        'checked_out': pool.checkedout(),
        'overflow': pool.overflow(),
        'max_overflow': settings.POSTGRES_MAX_OVERFLOW,
        'config': {
            'pool_size': settings.POSTGRES_POOL_SIZE,
            'max_overflow': settings.POSTGRES_MAX_OVERFLOW,
            'pool_recycle': settings.POSTGRES_POOL_RECYCLE,
            'pool_timeout': settings.POSTGRES_POOL_TIMEOUT
        }
    }

@router.get('/kiro-pool-status')
async def kiro_pool_status():
    """Kiro服务连接池状态监控接口"""
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

@router.get('/all-pool-status')
async def all_pool_status():
    """所有连接池状态监控接口"""
    # 获取数据库连接池状态
    pool = engine.pool
    db_status = {
        'status': 'active',
        'pool_size': pool.size(),
        'checked_in': pool.checkedin(),
        'checked_out': pool.checkedout(),
        'overflow': pool.overflow(),
        'max_overflow': settings.POSTGRES_MAX_OVERFLOW,
        'config': {
            'pool_size': settings.POSTGRES_POOL_SIZE,
            'max_overflow': settings.POSTGRES_MAX_OVERFLOW,
            'pool_recycle': settings.POSTGRES_POOL_RECYCLE,
            'pool_timeout': settings.POSTGRES_POOL_TIMEOUT
        }
    }

    # 获取Kiro服务连接池状态
    kiro_service = get_kiro_service()
    kiro_status = {}
    if kiro_service.is_initialized and kiro_service.session:
        connector = kiro_service.session.connector
        kiro_status = {
            'total_limit': connector.limit,
            'limit_per_host': connector.limit_per_host,
            'total_connections': len(connector._conns),
            'active_connections': sum(len(conns) for conns in connector._conns.values()),
            'connections_by_host': {
                host: len(conns)
                for host, conns in connector._conns.items()
            },
            'proxy': {
                'enabled': kiro_service.proxy is not None,
                'proxy_url': kiro_service.proxy if kiro_service.proxy else None
            }
        }

    return {
        'database': db_status,
        'kiro_service': kiro_status
    }
