# Pydantic模型定义
from pydantic import BaseModel, Field
from typing import Optional


# 用户管理相关模型
class UserBase(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    description: Optional[str] = None


class UserCreate(UserBase):
    password: str = Field(..., min_length=6)


class UserUpdate(BaseModel):
    description: Optional[str] = None


class UserResponse(UserBase):
    id: int

    class Config:
        from_attributes = True


class UserLogin(BaseModel):
    username: str
    password: str


# 账号管理相关模型
class AccountBase(BaseModel):
    account: str = Field(..., min_length=1, max_length=100)
    status: str = Field(default='active')
    description: Optional[str] = None
    token_info: Optional[str] = None  # JSON格式的认证信息


class AccountCreate(AccountBase):
    pass


class AccountUpdate(BaseModel):
    status: Optional[str] = None
    description: Optional[str] = None
    token_info: Optional[str] = None


class AccountResponse(AccountBase):
    id: int

    class Config:
        from_attributes = True


# API Key管理相关模型
class ApiKeyBase(BaseModel):
    api_key: str = Field(..., min_length=1)
    description: Optional[str] = None


class ApiKeyCreate(ApiKeyBase):
    pass


class ApiKeyUpdate(BaseModel):
    description: Optional[str] = None


class ApiKeyResponse(ApiKeyBase):
    id: int

    class Config:
        from_attributes = True


# 代理管理相关模型
class ProxyBase(BaseModel):
    proxy_type: str = Field(..., min_length=1)
    proxy_url: str = Field(..., min_length=1)
    proxy_port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None


class ProxyCreate(ProxyBase):
    pass


class ProxyUpdate(BaseModel):
    proxy_type: Optional[str] = None
    proxy_url: Optional[str] = None
    proxy_port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None


class ProxyResponse(ProxyBase):
    id: int

    class Config:
        from_attributes = True
