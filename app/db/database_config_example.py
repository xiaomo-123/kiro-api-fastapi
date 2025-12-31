
# 数据库配置示例
# 
# 本文件展示如何配置不同的数据库后端
# 默认使用 SQLite，适合开发和测试环境
# 生产环境建议使用 PostgreSQL 或 MySQL 以获得更好的性能和连接池支持

# SQLite 配置（默认）
# SQLite 使用 NullPool，不支持连接池参数
# 适合小型应用和开发环境
SQLITE_DATABASE_URL = "sqlite+aiosqlite:///./kiro_management.db"

# PostgreSQL 配置（推荐用于生产环境）
# 支持连接池，性能更好
# 需要安装: pip install asyncpg
POSTGRES_DATABASE_URL = "postgresql+asyncpg://user:password@localhost:5432/kiro_db"

# PostgreSQL 连接池配置示例
# 在 app/db/database.py 中使用以下配置：
"""
from sqlalchemy.ext.asyncio import create_async_engine

engine = create_async_engine(
    POSTGRES_DATABASE_URL,
    pool_size=20,              # 连接池大小
    max_overflow=10,           # 最大溢出连接数
    pool_pre_ping=True,        # 连接前检查连接是否有效
    pool_recycle=3600,         # 连接回收时间（秒）
    echo=False,
    future=True
)
"""

# MySQL 配置
# 支持连接池，性能较好
# 需要安装: pip install aiomysql
MYSQL_DATABASE_URL = "mysql+aiomysql://user:password@localhost:3306/kiro_db"

# MySQL 连接池配置示例
# 在 app/db/database.py 中使用以下配置：
"""
from sqlalchemy.ext.asyncio import create_async_engine

engine = create_async_engine(
    MYSQL_DATABASE_URL,
    pool_size=20,              # 连接池大小
    max_overflow=10,           # 最大溢出连接数
    pool_pre_ping=True,        # 连接前检查连接是否有效
    pool_recycle=3600,         # 连接回收时间（秒）
    echo=False,
    future=True
)
"""

# 连接池参数说明
"""
pool_size: 连接池中保持的连接数
max_overflow: 超过 pool_size 时最多可以创建的额外连接数
pool_pre_ping: 每次从连接池获取连接时是否检查连接有效性
pool_recycle: 连接回收时间（秒），防止连接长时间未使用导致失效

建议配置：
- 小型应用: pool_size=5, max_overflow=5
- 中型应用: pool_size=20, max_overflow=10
- 大型应用: pool_size=50, max_overflow=20
"""
