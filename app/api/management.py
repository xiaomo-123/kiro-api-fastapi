# 管理系统API路由
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
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

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# 用户管理路由
@router.post("/users/login", response_model=dict)
async def login(user_login: UserLogin, db: Session = Depends(get_db)):
    """用户登录"""
    user = db.query(User).filter(User.username == user_login.username).first()
    if not user or not pwd_context.verify(user_login.password, user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误"
        )
    return {"message": "登录成功", "user_id": user.id}


@router.post("/users", response_model=UserResponse)
async def create_user(user: UserCreate, db: Session = Depends(get_db)):
    """创建用户"""
    # 检查用户名是否已存在
    existing_user = db.query(User).filter(User.username == user.username).first()
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
    db.commit()
    db.refresh(db_user)
    return db_user


@router.get("/users", response_model=List[UserResponse])
async def get_users(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """获取用户列表"""
    users = db.query(User).offset(skip).limit(limit).all()
    return users


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(user_id: int, db: Session = Depends(get_db)):
    """获取单个用户"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )
    return user


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(user_id: int, user_update: UserUpdate, db: Session = Depends(get_db)):
    """更新用户"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )

    if user_update.description is not None:
        user.description = user_update.description

    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, db: Session = Depends(get_db)):
    """删除用户"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )

    db.delete(user)
    db.commit()
    return {"message": "用户删除成功"}


# 账号管理路由
@router.post("/accounts", response_model=AccountResponse)
async def create_account(account: AccountCreate, db: Session = Depends(get_db)):
    """创建账号"""
    try:
        # 验证account字段是否为有效的JSON
        import json
        account_data = json.loads(account.account)
        
        # 验证必需字段
        required_fields = ['accessToken', 'refreshToken', 'profileArn']
        optional_fields = ['clientId', 'clientSecret']
        missing_fields = [field for field in required_fields if field not in account_data]
        if missing_fields:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"账号数据缺少必需字段: {missing_fields}"
            )
        
        # 验证expiresAt字段（如果存在）
        if 'expiresAt' in account_data:
            try:
                from datetime import datetime
                datetime.fromisoformat(account_data['expiresAt'].replace('Z', '+00:00'))
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="expiresAt字段格式无效，应为ISO 8601格式"
                )
        
        # 创建账号
        db_account = Account(
            account=account.account,
            status=account.status,
            description=account.description
        )
        db.add(db_account)
        db.commit()
        db.refresh(db_account)
        return db_account
    
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"账号数据必须是有效的JSON格式: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"创建账号失败: {str(e)}"
        )


@router.get("/accounts", response_model=List[AccountResponse])
async def get_accounts(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """获取账号列表"""
    accounts = db.query(Account).offset(skip).limit(limit).all()
    return accounts


@router.get("/accounts/{account_id}", response_model=AccountResponse)
async def get_account(account_id: int, db: Session = Depends(get_db)):
    """获取单个账号"""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="账号不存在"
        )
    return account


@router.put("/accounts/{account_id}", response_model=AccountResponse)
async def update_account(account_id: int, account_update: AccountUpdate, db: Session = Depends(get_db)):
    """更新账号"""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="账号不存在"
        )    
    # 如果更新account字段，验证JSON格式
    if account_update.account is not None:
        try:
            import json
            account_data = json.loads(account_update.account)
            
            # 验证必需字段
            required_fields = ['accessToken', 'refreshToken', 'profileArn']
            optional_fields = ['clientId', 'clientSecret']
            missing_fields = [field for field in required_fields if field not in account_data]
            if missing_fields:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"账号数据缺少必需字段: {missing_fields}"
                )
            
            # 验证expiresAt字段（如果存在）
            if 'expiresAt' in account_data:
                try:
                    from datetime import datetime
                    datetime.fromisoformat(account_data['expiresAt'].replace('Z', '+00:00'))
                except Exception:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="expiresAt字段格式无效，应为ISO 8601格式"
                    )
            
            account.account = account_update.account
        
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"账号数据必须是有效的JSON格式: {str(e)}"
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"更新账号失败: {str(e)}"
            )
    
    if account_update.status is not None:
        account.status = account_update.status
    if account_update.description is not None:
        account.description = account_update.description

    db.commit()
    db.refresh(account)
    return account


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: int, db: Session = Depends(get_db)):
    """删除账号"""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="账号不存在"
        )

    db.delete(account)
    db.commit()
    return {"message": "账号删除成功"}


@router.post("/accounts/batch-delete")
async def batch_delete_accounts(request: AccountBatchDelete, db: Session = Depends(get_db)):
    """批量删除账号"""
    ids = request.ids

    if not ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请提供要删除的账号ID列表"
        )

    # 查询要删除的账号
    accounts = db.query(Account).filter(Account.id.in_(ids)).all()

    if not accounts:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="未找到要删除的账号"
        )

    # 批量删除
    for account in accounts:
        db.delete(account)

    db.commit()

    return {
        "message": f"成功删除 {len(accounts)} 个账号",
        "deleted_count": len(accounts)
    }


@router.post("/accounts/import")
async def import_accounts(accounts: List[dict], db: Session = Depends(get_db)):
    """批量导入账号"""
    success_count = 0
    error_count = 0
    errors = []

    for idx, account_data in enumerate(accounts, 1):
        try:
            # 将JSON对象转换为字符串存储
            account_str = str(account_data)

            db_account = Account(
                account=account_str,
                status='1',
                description=f'导入自JSON - 第{idx}条'
            )
            db.add(db_account)
            db.commit()
            success_count += 1
        except Exception as e:
            error_count += 1
            errors.append(f"第{idx}条导入失败: {str(e)}")
            db.rollback()

    return {
        "message": f"导入完成，成功{success_count}条，失败{error_count}条",
        "success_count": success_count,
        "error_count": error_count,
        "errors": errors
    }


# API Key管理路由
@router.post("/apikeys", response_model=ApiKeyResponse)
async def create_apikey(apikey: ApiKeyCreate, db: Session = Depends(get_db)):
    """创建API Key"""
    existing_apikey = db.query(ApiKey).filter(ApiKey.api_key == apikey.api_key).first()
    if existing_apikey:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="API Key已存在"
        )

    db_apikey = ApiKey(
        api_key=apikey.api_key,
        description=apikey.description,
        status=apikey.status
    )
    db.add(db_apikey)
    db.commit()
    db.refresh(db_apikey)
    return db_apikey


@router.get("/apikeys", response_model=List[ApiKeyResponse])
async def get_apikeys(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """获取API Key列表"""
    apikeys = db.query(ApiKey).offset(skip).limit(limit).all()
    return apikeys


@router.get("/apikeys/{apikey_id}", response_model=ApiKeyResponse)
async def get_apikey(apikey_id: int, db: Session = Depends(get_db)):
    """获取单个API Key"""
    apikey = db.query(ApiKey).filter(ApiKey.id == apikey_id).first()
    if not apikey:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API Key不存在"
        )
    return apikey


@router.put("/apikeys/{apikey_id}", response_model=ApiKeyResponse)
async def update_apikey(apikey_id: int, apikey_update: ApiKeyUpdate, db: Session = Depends(get_db)):
    """更新API Key"""
    apikey = db.query(ApiKey).filter(ApiKey.id == apikey_id).first()
    if not apikey:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API Key不存在"
        )

    if apikey_update.api_key is not None:
        # 检查新的api_key是否与其他记录冲突
        existing_apikey = db.query(ApiKey).filter(
            ApiKey.api_key == apikey_update.api_key,
            ApiKey.id != apikey_id
        ).first()
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

    db.commit()
    db.refresh(apikey)
    return apikey


@router.delete("/apikeys/{apikey_id}")
async def delete_apikey(apikey_id: int, db: Session = Depends(get_db)):
    """删除API Key"""
    apikey = db.query(ApiKey).filter(ApiKey.id == apikey_id).first()
    if not apikey:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API Key不存在"
        )

    db.delete(apikey)
    db.commit()
    return {"message": "API Key删除成功"}


# 代理管理路由
@router.post("/proxies", response_model=ProxyResponse)
async def create_proxy(proxy: ProxyCreate, db: Session = Depends(get_db)):
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
    db.commit()
    db.refresh(db_proxy)
    return db_proxy


@router.get("/proxies", response_model=List[ProxyResponse])
async def get_proxies(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """获取代理列表"""
    proxies = db.query(Proxy).offset(skip).limit(limit).all()
    return proxies


@router.get("/proxies/{proxy_id}", response_model=ProxyResponse)
async def get_proxy(proxy_id: int, db: Session = Depends(get_db)):
    """获取单个代理"""
    proxy = db.query(Proxy).filter(Proxy.id == proxy_id).first()
    if not proxy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="代理不存在"
        )
    return proxy


@router.put("/proxies/{proxy_id}", response_model=ProxyResponse)
async def update_proxy(proxy_id: int, proxy_update: ProxyUpdate, db: Session = Depends(get_db)):
    """更新代理"""
    proxy = db.query(Proxy).filter(Proxy.id == proxy_id).first()
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

    db.commit()
    db.refresh(proxy)
    return proxy


@router.delete("/proxies/{proxy_id}")
async def delete_proxy(proxy_id: int, db: Session = Depends(get_db)):
    """删除代理"""
    proxy = db.query(Proxy).filter(Proxy.id == proxy_id).first()
    if not proxy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="代理不存在"
        )

    db.delete(proxy)
    db.commit()
    return {"message": "代理删除成功"}
