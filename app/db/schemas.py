# Pydantic模型定义
from pydantic import BaseModel, Field
from typing import Optional, List


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
    account: str = Field(..., min_length=1)
    status: str = Field(default='1')
    description: Optional[str] = None


class AccountCreate(AccountBase):
    pass


class AccountUpdate(BaseModel):
    account: Optional[str] = None
    status: Optional[str] = None
    description: Optional[str] = None


class AccountResponse(AccountBase):
    id: int

    class Config:
        from_attributes = True


class AccountBatchDelete(BaseModel):
    ids: List[int] = Field(..., min_items=1)


# API Key管理相关模型
class ApiKeyBase(BaseModel):
    api_key: str = Field(..., min_length=1)
    description: Optional[str] = None
    status: str = Field(default='1')


class ApiKeyCreate(ApiKeyBase):
    pass


class ApiKeyUpdate(BaseModel):
    api_key: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None


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
    status: str = Field(default='1')


class ProxyCreate(ProxyBase):
    pass


class ProxyUpdate(BaseModel):
    proxy_type: Optional[str] = None
    proxy_url: Optional[str] = None
    proxy_port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    status: Optional[str] = None


class ProxyResponse(ProxyBase):
    id: int
    
    class Config:
        from_attributes = True
        json_encoders = {
            # 确保status字段正确序列化
            str: lambda v: v
        }
