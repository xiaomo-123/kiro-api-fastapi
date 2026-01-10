
# 管理系统API路由（异步版本）
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from passlib.context import CryptContext
from typing import List, Optional
import json
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
from ..services.proxy_pool import (
    create_proxy,
    get_proxies,
    get_proxy,
    update_proxys,
    delete_proxy,
    batch_delete_proxies,
    import_proxies
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



@router.post("/apikeys", response_model=ApiKeyResponse)
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

    try:
        # 同步到 Redis
        from ..services.apikey_manager import add_apikey_to_redis
        await add_apikey_to_redis(db_apikey)
    except Exception as e:
        # Redis 同步失败不影响数据库操作，只记录日志
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to sync API Key to Redis: {e}", exc_info=True)

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

    # 如果要更新api_key，检查新的api_key是否与其他记录重复
    if apikey_update.api_key is not None and apikey_update.api_key != apikey.api_key:
        stmt = select(ApiKey).filter(ApiKey.api_key == apikey_update.api_key)
        result = await db.execute(stmt)
        existing_apikey = result.scalars().first()
        if existing_apikey:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="API Key已存在"
            )
        apikey.api_key = apikey_update.api_key

    if apikey_update.description is not None:
        apikey.description = apikey_update.description
    if apikey_update.status is not None:
        apikey.status = apikey_update.status

    await db.commit()
    await db.refresh(apikey)

    try:
        # 同步到 Redis
        from ..services.apikey_manager import update_apikey_in_redis
        await update_apikey_in_redis(apikey)
    except Exception as e:
        # Redis 同步失败不影响数据库操作，只记录日志
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to sync API Key to Redis: {e}", exc_info=True)

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

    try:
        # 从 Redis 中移除
        from ..services.apikey_manager import delete_apikey_from_redis
        await delete_apikey_from_redis(apikey.api_key)
    except Exception as e:
        # Redis 同步失败不影响数据库操作，只记录日志
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to delete API Key from Redis: {e}", exc_info=True)

    return {"message": "API Key删除成功"}


# 代理管理路由
@router.post("/proxies", response_model=ProxyResponse)
async def create_proxy_endpoint(proxy: ProxyCreate):
    """创建代理"""
    try:
        return await create_proxy(
            proxy_type=proxy.proxy_type,
            proxy_url=proxy.proxy_url,
            proxy_port=proxy.proxy_port,
            username=proxy.username,
            password=proxy.password,
            status=proxy.status
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"创建代理失败: {str(e)}"
        )


@router.get("/proxies", response_model=List[ProxyResponse])
async def get_proxies_endpoint(skip: int = 0, limit: int = 100):
    """获取代理列表"""
    try:
        return await get_proxies(skip=skip, limit=limit)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取代理列表失败: {str(e)}"
        )


@router.get("/proxies/{proxy_id}", response_model=ProxyResponse)
async def get_proxy_endpoint(proxy_id: int):
    """获取单个代理"""
    try:
        proxy = await get_proxy(proxy_id)
        if not proxy:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="代理不存在"
            )
        return proxy
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取代理失败: {str(e)}"
        )


@router.put("/proxies/{proxy_id}", response_model=ProxyResponse)
async def update_proxy_endpoint(proxy_id: int, proxy_update: ProxyUpdate):
    """更新代理"""
    try:
        
        
        proxy = await update_proxys(
            proxy_id=proxy_id,
            proxy_type=proxy_update.proxy_type,
            proxy_url=proxy_update.proxy_url,
            proxy_port=proxy_update.proxy_port,
            username=proxy_update.username,
            password=proxy_update.password,
            status=proxy_update.status
        )
        if not proxy:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="代理不存在"
            )
        return proxy
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新代理失败: {str(e)}"
        )


@router.delete("/proxies/{proxy_id}")
async def delete_proxy_endpoint(proxy_id: int):
    """删除代理"""
    try:
        success = await delete_proxy(proxy_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="代理不存在"
            )
        return {"message": "代理删除成功"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"删除代理失败: {str(e)}"
        )


@router.post("/proxies/batch-delete")
async def batch_delete_proxies_endpoint(request: AccountBatchDelete):
    """批量删除代理"""
    try:
        return await batch_delete_proxies(request.ids)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"批量删除代理失败: {str(e)}"
        )


@router.post("/proxies/import")
async def import_proxies_endpoint(proxies: List[dict]):
    """批量导入代理"""
    try:
        return await import_proxies(proxies)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"批量导入代理失败: {str(e)}"
        )
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
async def get_accounts_endpoint(skip: int = 0, limit: int = 100, status: str = None):
    """获取账号列表"""
    try:
        return await get_accounts(skip=skip, limit=limit, status=status)
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

@router.delete("/accounts/delete-all")
async def delete_all_accounts_endpoint():
    """删除所有账号"""
    try:
        # 获取所有账号ID
        all_accounts = await get_accounts(skip=0, limit=10000)
        account_ids = [account.id for account in all_accounts]
        
        if not account_ids:
            return {"message": "没有账号需要删除"}
        
        # 批量删除所有账号
        result = await batch_delete_accounts(account_ids)
        return {"message": f"成功删除{result.get('deleted_count', 0)}个账号"}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"删除所有账号失败: {str(e)}"
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


@router.get("/accounts/export/json")
async def export_accounts_endpoint(account_status: Optional[str] = Query(None, alias="status")):
    """导出账号列表为JSON格式"""
    try:
        # 获取所有符合条件的账号（不分页）
        # 使用一个很大的limit值而不是None，避免SQLAlchemy的limit问题
        accounts = await get_accounts(skip=0, limit=10000, status=account_status)
        
        # 解析每个账号的account字段（JSON格式）并导出
        exported_accounts = []
        for account in accounts:
            try:
                account_data = json.loads(account.account)
                exported_accounts.append(account_data)
            except json.JSONDecodeError as e:
                # 如果解析失败，记录错误并跳过该账号
                print(f"Failed to parse account {account.id}: {e}")
                continue
            except Exception as e:
                # 其他异常也记录并跳过
                print(f"Error processing account {account.id}: {e}")
                continue
        
        return exported_accounts
    except Exception as e:
        print(f"Export accounts error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"导出账号失败: {str(e)}"
        )


@router.post("/system/sync-sequences")
async def sync_sequences_endpoint():
    """同步数据库序列，解决ID冲突问题"""
    try:
        from ..db.sync_sequences import sync_all_sequences
        success = await sync_all_sequences()
        if success:
            return {"message": "数据库序列同步成功"}
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="数据库序列同步失败"
            )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"同步序列失败: {str(e)}"
        )


@router.post("/system/check-sequences")
async def check_sequences_endpoint():
    """检查数据库序列状态"""
    try:
        from ..db.sync_sequences import check_sequences
        await check_sequences()
        return {"message": "序列状态检查完成"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"检查序列失败: {str(e)}"
        )


@router.post("/system/force-sync-sequences")
async def force_sync_sequences_endpoint():
    """强制同步数据库序列"""
    try:
        from ..db.sync_sequences import force_sync_sequences
        success = await force_sync_sequences()
        if success:
            return {"message": "数据库序列强制同步成功"}
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="数据库序列强制同步失败"
            )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"强制同步序列失败: {str(e)}"
        )

