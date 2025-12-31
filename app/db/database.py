# 数据库配置和初始化
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import sessionmaker
from .models import Base
import os

# SQLite数据库文件路径，支持通过环境变量配置
DB_PATH = os.getenv("DB_PATH", "./kiro_management.db")
# 异步数据库 URL
SQLALCHEMY_DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

# 创建异步数据库引擎
# 注意：SQLite 使用 NullPool，不支持 pool_size 和 max_overflow 参数
# 如果需要连接池，建议使用 PostgreSQL 或 MySQL
engine = create_async_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={
        "check_same_thread": False,
    },
    echo=False,                # 不输出SQL日志
    future=True                # 使用 SQLAlchemy 2.0 风格
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
