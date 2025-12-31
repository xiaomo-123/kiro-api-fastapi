
# 管理系统API路由（异步版本）
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from passlib.context import CryptContext
from typing import List

from ..db.database import get_db
from ..db.models import User, Account, ApiKey, Proxy
from ..db.schemas import (
    UserCreate, UserUpdate, UserResponse, UserLogin,
    AccountCreate, AccountUpdate, AccountResponse, AccountBatchDelete,
    ApiKeyCreate, ApiKeyUpdate, ApiKeyResponse,
    ProxyCreate, ProxyUpdate, ProxyResponse
)
from ..services.account_pool import (
    create_account,
    get_accounts,
    get_account,
    update_account,
    delete_account,
    batch_delete_accounts,
    import_accounts
)
router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# 用户管理路由
@router.post("/users/login", response_model=dict)
async def login(user_login: UserLogin, db: AsyncSession = Depends(get_db)):
    """用户登录"""
    stmt = select(User).filter(User.username == user_login.username)
    result = await db.execute(stmt)
    user = result.scalars().first()
    if not user or not pwd_context.verify(user_login.password, user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误"
        )
    return {"message": "登录成功", "user_id": user.id}


@router.post("/users", response_model=UserResponse)
async def create_user(user: UserCreate, db: AsyncSession = Depends(get_db)):
    """创建用户"""
    # 检查用户名是否已存在
    stmt = select(User).filter(User.username == user.username)
    result = await db.execute(stmt)
    existing_user = result.scalars().first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名已存在"
        )

    # 创建新用户
    # 确保密码不超过72字节（bcrypt限制）
    password = user.password[:72]
    hashed_password = pwd_context.hash(password)
    db_user = User(
        username=user.username,
        password=hashed_password,
        description=user.description
    )
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user


@router.get("/users", response_model=List[UserResponse])
async def get_users(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)):
    """获取用户列表"""
    stmt = select(User).offset(skip).limit(limit)
    result = await db.execute(stmt)
    users = result.scalars().all()
    return users


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(user_id: int, db: AsyncSession = Depends(get_db)):
    """获取单个用户"""
    stmt = select(User).filter(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )
    return user


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(user_id: int, user_update: UserUpdate, db: AsyncSession = Depends(get_db)):
    """更新用户"""
    stmt = select(User).filter(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )

    if user_update.description is not None:
        user.description = user_update.description

    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, db: AsyncSession = Depends(get_db)):
    """删除用户"""
    stmt = select(User).filter(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )
    await db.delete(user)
    await db.commit()
    return {"message": "用户删除成功"}



async def create_apikey(apikey: ApiKeyCreate, db: AsyncSession = Depends(get_db)):
    """创建API Key"""
    # 检查API Key是否已存在
    stmt = select(ApiKey).filter(ApiKey.api_key == apikey.api_key)
    result = await db.execute(stmt)
    existing_apikey = result.scalars().first()
    if existing_apikey:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="API Key已存在"
        )

    # 创建API Key
    db_apikey = ApiKey(
        api_key=apikey.api_key,
        description=apikey.description,
        status=apikey.status
    )
    db.add(db_apikey)
    await db.commit()
    await db.refresh(db_apikey)
    return db_apikey


@router.get("/apikeys", response_model=List[ApiKeyResponse])
async def get_apikeys(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)):
    """获取API Key列表"""
    stmt = select(ApiKey).offset(skip).limit(limit)
    result = await db.execute(stmt)
    apikeys = result.scalars().all()
    return apikeys


@router.get("/apikeys/{apikey_id}", response_model=ApiKeyResponse)
async def get_apikey(apikey_id: int, db: AsyncSession = Depends(get_db)):
    """获取单个API Key"""
    stmt = select(ApiKey).filter(ApiKey.id == apikey_id)
    result = await db.execute(stmt)
    apikey = result.scalars().first()
    if not apikey:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API Key不存在"
        )
    return apikey


@router.put("/apikeys/{apikey_id}", response_model=ApiKeyResponse)
async def update_apikey(apikey_id: int, apikey_update: ApiKeyUpdate, db: AsyncSession = Depends(get_db)):
    """更新API Key"""
    stmt = select(ApiKey).filter(ApiKey.id == apikey_id)
    result = await db.execute(stmt)
    apikey = result.scalars().first()
    if not apikey:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API Key不存在"
        )

    if apikey_update.description is not None:
        apikey.description = apikey_update.description
    if apikey_update.status is not None:
        apikey.status = apikey_update.status

    await db.commit()
    await db.refresh(apikey)
    return apikey


@router.delete("/apikeys/{apikey_id}")
async def delete_apikey(apikey_id: int, db: AsyncSession = Depends(get_db)):
    """删除API Key"""
    stmt = select(ApiKey).filter(ApiKey.id == apikey_id)
    result = await db.execute(stmt)
    apikey = result.scalars().first()
    if not apikey:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API Key不存在"
        )
    await db.delete(apikey)
    await db.commit()
    return {"message": "API Key删除成功"}


# 代理管理路由
@router.post("/proxies", response_model=ProxyResponse)
async def create_proxy(proxy: ProxyCreate, db: AsyncSession = Depends(get_db)):
    """创建代理"""
    db_proxy = Proxy(
        proxy_type=proxy.proxy_type,
        proxy_url=proxy.proxy_url,
        proxy_port=proxy.proxy_port,
        username=proxy.username,
        password=proxy.password,
        status=proxy.status
    )
    db.add(db_proxy)
    await db.commit()
    await db.refresh(db_proxy)
    return db_proxy


@router.get("/proxies", response_model=List[ProxyResponse])
async def get_proxies(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)):
    """获取代理列表"""
    stmt = select(Proxy).offset(skip).limit(limit)
    result = await db.execute(stmt)
    proxies = result.scalars().all()
    return proxies


@router.get("/proxies/{proxy_id}", response_model=ProxyResponse)
async def get_proxy(proxy_id: int, db: AsyncSession = Depends(get_db)):
    """获取单个代理"""
    stmt = select(Proxy).filter(Proxy.id == proxy_id)
    result = await db.execute(stmt)
    proxy = result.scalars().first()
    if not proxy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="代理不存在"
        )
    return proxy


@router.put("/proxies/{proxy_id}", response_model=ProxyResponse)
async def update_proxy(proxy_id: int, proxy_update: ProxyUpdate, db: AsyncSession = Depends(get_db)):
    """更新代理"""
    stmt = select(Proxy).filter(Proxy.id == proxy_id)
    result = await db.execute(stmt)
    proxy = result.scalars().first()
    if not proxy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="代理不存在"
        )

    if proxy_update.proxy_type is not None:
        proxy.proxy_type = proxy_update.proxy_type
    if proxy_update.proxy_url is not None:
        proxy.proxy_url = proxy_update.proxy_url
    if proxy_update.proxy_port is not None:
        proxy.proxy_port = proxy_update.proxy_port
    if proxy_update.username is not None:
        proxy.username = proxy_update.username
    if proxy_update.password is not None:
        proxy.password = proxy_update.password
    if proxy_update.status is not None:
        proxy.status = proxy_update.status

    await db.commit()
    await db.refresh(proxy)
    return proxy


@router.delete("/proxies/{proxy_id}")
async def delete_proxy(proxy_id: int, db: AsyncSession = Depends(get_db)):
    """删除代理"""
    stmt = select(Proxy).filter(Proxy.id == proxy_id)
    result = await db.execute(stmt)
    proxy = result.scalars().first()
    if not proxy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="代理不存在"
        )
    await db.delete(proxy)
    await db.commit()
    return {"message": "代理删除成功"}
# 账号管理路由
@router.post("/accounts", response_model=AccountResponse)
async def create_account_endpoint(account: AccountCreate):
    """创建账号"""
    try:
        return await create_account(
            account_data=account.account,
            status=account.status,
            description=account.description
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"创建账号失败: {str(e)}"
        )

@router.get("/accounts", response_model=List[AccountResponse])
async def get_accounts_endpoint(skip: int = 0, limit: int = 100):
    """获取账号列表"""
    try:
        return await get_accounts(skip=skip, limit=limit)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取账号列表失败: {str(e)}"
        )

@router.get("/accounts/{account_id}", response_model=AccountResponse)
async def get_account_endpoint(account_id: int):
    """获取单个账号"""
    try:
        account = await get_account(account_id)
        if not account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="账号不存在"
            )
        return account
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取账号失败: {str(e)}"
        )

@router.put("/accounts/{account_id}", response_model=AccountResponse)
async def update_account_endpoint(account_id: int, account_update: AccountUpdate):
    """更新账号"""
    try:
        account = await update_account(
            account_id=account_id,
            account_data=account_update.account,
            status=account_update.status,
            description=account_update.description
        )
        if not account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="账号不存在"
            )
        return account
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新账号失败: {str(e)}"
        )

@router.delete("/accounts/{account_id}")
async def delete_account_endpoint(account_id: int):
    """删除账号"""
    try:
        success = await delete_account(account_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="账号不存在"
            )
        return {"message": "账号删除成功"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"删除账号失败: {str(e)}"
        )

@router.post("/accounts/batch-delete")
async def batch_delete_accounts_endpoint(request: AccountBatchDelete):
    """批量删除账号"""
    try:
        return await batch_delete_accounts(request.ids)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"批量删除账号失败: {str(e)}"
        )

@router.post("/accounts/import")
async def import_accounts_endpoint(accounts: List[dict]):
    """批量导入账号"""
    try:
        return await import_accounts(accounts)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"批量导入账号失败: {str(e)}"
        )


