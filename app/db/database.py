# 数据库配置和初始化
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
from .models import Base
from ..config import settings
import logging

logger = logging.getLogger(__name__)

# PostgreSQL数据库URL
SQLALCHEMY_DATABASE_URL = f"postgresql+asyncpg://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"

# 创建异步数据库引擎
# 使用PostgreSQL连接池提升并发性能
engine = create_async_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_size=getattr(settings, "POSTGRES_POOL_SIZE", 200),
    max_overflow=getattr(settings, "POSTGRES_MAX_OVERFLOW", 200),
    pool_pre_ping=True,
    pool_recycle=getattr(settings, "POSTGRES_POOL_RECYCLE", 1800),
    pool_timeout=getattr(settings, "POSTGRES_POOL_TIMEOUT", 30),
    echo=False,
    future=True
)

# 创建异步会话工厂
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

# 保留同步会话工厂用于向后兼容
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine.sync_engine if hasattr(engine, 'sync_engine') else engine)


async def init_db():
    """初始化数据库，创建所有表"""
    try:
        # 测试数据库连接
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            logger.info("数据库连接成功")

        # 初始化数据库结构
        async with engine.begin() as conn:
            # 先创建序列（分别执行每个语句）
            await conn.execute(text("CREATE SEQUENCE IF NOT EXISTS users_id_seq START 1"))
            await conn.execute(text("CREATE SEQUENCE IF NOT EXISTS accounts_id_seq START 1"))
            await conn.execute(text("CREATE SEQUENCE IF NOT EXISTS api_keys_id_seq START 1"))
            await conn.execute(text("CREATE SEQUENCE IF NOT EXISTS proxies_id_seq START 1"))
            # 然后创建表
            await conn.run_sync(Base.metadata.create_all)
            logger.info("数据库表结构创建成功")
    except Exception as e:
        logger.error(f"数据库初始化失败: {str(e)}")
        raise


async def get_db():
    """获取异步数据库会话"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def get_sync_db():
    """获取同步数据库会话（向后兼容）"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
