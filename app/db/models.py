# 数据库模型定义
from sqlalchemy import Column, Integer, String, Boolean, Text
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class User(Base):
    """用户管理表"""
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    description = Column(String(255))


class Account(Base):
    """账号管理表"""
    __tablename__ = 'accounts'

    id = Column(Integer, primary_key=True, autoincrement=True)
    account = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default='1')
    description = Column(String(255))


class ApiKey(Base):
    """API Key管理表"""
    __tablename__ = 'api_keys'

    id = Column(Integer, primary_key=True, autoincrement=True)
    api_key = Column(String(255), unique=True, nullable=False)
    description = Column(String(255))
    status = Column(String(20), nullable=False, default='1')


class Proxy(Base):
    """代理管理表"""
    __tablename__ = 'proxies'

    id = Column(Integer, primary_key=True, autoincrement=True)
    proxy_type = Column(String(20), nullable=False)
    proxy_url = Column(String(255), nullable=False)
    proxy_port = Column(Integer)
    username = Column(String(100))
    password = Column(String(255))
    status = Column(String(20), nullable=False, default='1')
