# 账号池管理API
from fastapi import APIRouter, Depends, HTTPException, status
from typing import Dict
import logging

from ..services.account_pool_v2 import (
    initialize_pool,
    get_available_account,
    release_account,
    update_account,
    get_pool_stats as get_pool_status,
    health_check,
    close_redis
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/pool/initialize")
async def initialize_account_pool():
    """初始化账号池"""
    try:
        await initialize_pool()
        return {"message": "账号池初始化成功"}
    except Exception as e:
        logger.error(f"Failed to initialize pool: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"初始化失败: {str(e)}"
        )


@router.get("/pool/account")
async def get_account():
    """获取可用账号"""
    try:
        account = await get_available_account()
        if not account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="暂无可用账号"
            )
        return account
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get account: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取账号失败: {str(e)}"
        )


@router.post("/pool/release/{account_id}")
async def release_account_endpoint(account_id: int, success: bool = True, response_time: float = 0):
    """释放账号"""
    try:
        await release_account(account_id, success, response_time)
        return {"message": "账号释放成功"}
    except Exception as e:
        logger.error(f"Failed to release account: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"释放账号失败: {str(e)}"
        )


@router.put("/pool/account/{account_id}")
async def update_account_endpoint(account_id: int, account_data: Dict):
    """更新账号信息"""
    try:
        await update_account(account_id, account_data)
        return {"message": "账号更新成功"}
    except Exception as e:
        logger.error(f"Failed to update account: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新账号失败: {str(e)}"
        )


@router.get("/pool/status")
async def get_status():
    """获取账号池状态"""
    try:
        status = await get_pool_status()
        return status
    except Exception as e:
        logger.error(f"Failed to get pool status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取状态失败: {str(e)}"
        )


@router.post("/pool/health-check")
async def run_health_check():
    """执行健康检查"""
    try:
        await health_check()
        return {"message": "健康检查完成"}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"健康检查失败: {str(e)}"
        )
