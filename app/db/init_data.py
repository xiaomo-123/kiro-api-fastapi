# 初始化默认数据
from passlib.context import CryptContext
from sqlalchemy import select
from .database import AsyncSessionLocal
from .models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


async def init_default_user():
    """初始化默认管理员用户"""
    async with AsyncSessionLocal() as db:
        try:
            # 检查是否已存在管理员用户
            stmt = select(User).filter(User.username == 'admin')
            result = await db.execute(stmt)
            admin_user = result.scalars().first()

            if not admin_user:
                # 创建默认管理员用户
                # 确保密码不超过72字节（bcrypt限制）
                password = 'admin123'[:72]
                hashed_password = pwd_context.hash(password)
                admin_user = User(
                    username='admin',
                    password=hashed_password,
                    description='系统管理员'
                )
                db.add(admin_user)
                await db.commit()
                print('默认管理员用户创建成功')
                print('用户名: admin')
                print('密码: admin123')
           
        except Exception as e:
            print(f'初始化默认用户失败: {e}')
            await db.rollback()


if __name__ == '__main__':
    import asyncio
    asyncio.run(init_default_user())
