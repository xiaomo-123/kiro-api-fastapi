# 数据库配置和初始化
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
from .models import Base
from ..config import settings

# PostgreSQL数据库URL
SQLALCHEMY_DATABASE_URL = f"postgresql+asyncpg://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"

# 创建异步数据库引擎
# 使用PostgreSQL连接池提升并发性能
engine = create_async_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_size=settings.POSTGRES_POOL_SIZE,
    max_overflow=settings.POSTGRES_MAX_OVERFLOW,
    pool_pre_ping=True,
    pool_recycle=settings.POSTGRES_POOL_RECYCLE,
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
    async with engine.begin() as conn:
        # 先创建序列（分别执行每个语句）
        await conn.execute(text("CREATE SEQUENCE IF NOT EXISTS users_id_seq START 1"))
        await conn.execute(text("CREATE SEQUENCE IF NOT EXISTS accounts_id_seq START 1"))
        await conn.execute(text("CREATE SEQUENCE IF NOT EXISTS api_keys_id_seq START 1"))
        await conn.execute(text("CREATE SEQUENCE IF NOT EXISTS proxies_id_seq START 1"))
        # 然后创建表
        await conn.run_sync(Base.metadata.create_all)


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
