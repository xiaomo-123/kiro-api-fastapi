# 初始化默认数据
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from .database import SessionLocal
from .models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def init_default_user():
    """初始化默认管理员用户"""
    db: Session = SessionLocal()
    try:
        # 检查是否已存在管理员用户
        admin_user = db.query(User).filter(User.username == 'admin').first()
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
            db.commit()
            print('默认管理员用户创建成功')
            print('用户名: admin')
            print('密码: admin123')
        else:
            print('管理员用户已存在')
    except Exception as e:
        print(f'初始化默认用户失败: {e}')
        db.rollback()
    finally:
        db.close()


if __name__ == '__main__':
    init_default_user()
