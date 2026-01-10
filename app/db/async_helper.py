
# 异步数据库操作辅助函数
import logging
from typing import List, Dict, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from .models import Account, Proxy, ApiKey
from .database import get_db

logger = logging.getLogger(__name__)


async def load_accounts_from_db() -> List[Dict]:
    """从数据库加载所有状态为1的账号"""
    try:
        async for db in get_db():
            try:
                # 获取所有状态为1的账号
                stmt = select(Account).filter(Account.status == '1')
                result = await db.execute(stmt)
                accounts = result.scalars().all()

                if not accounts:
                    logger.warning('[Kiro] No accounts with status=1 found in database')
                    return []

                # 解析账号数据
                accounts_list = []
                import json
                import ast
                from datetime import datetime

                for acc in accounts:
                    try:
                        # account字段存储的是JSON字符串，需要解析
                        if not acc.account or not acc.account.strip():
                            logger.error(f'[Kiro] Account {acc.id} has empty account data')
                            continue

                        # 尝试解析JSON，支持单引号和双引号
                        account_str = acc.account.strip()
                        try:
                            # 首先尝试标准JSON解析
                            account_data = json.loads(account_str)
                        except json.JSONDecodeError:
                            # 如果失败，尝试使用ast.literal_eval解析（支持单引号）
                            try:
                                account_data = ast.literal_eval(account_str)
                            except (ValueError, SyntaxError) as e:
                                logger.error(f'[Kiro] Account {acc.id} failed to parse with both json and ast: {e}')
                                raise
                        account_data['id'] = acc.id
                        account_data['description'] = acc.description

                        # 验证必需字段
                        required_fields = ['accessToken', 'refreshToken', 'profileArn']
                        optional_fields = ['clientId', 'clientSecret']
                        missing_fields = [field for field in required_fields if field not in account_data]
                        if missing_fields:
                            logger.error(f'[Kiro] Account {acc.id} (description: {acc.description}) missing required fields: {missing_fields}')
                            logger.error(f'[Kiro] Account {acc.id} available fields: {list(account_data.keys())}')
                            continue

                        # 记录缺少的可选字段
                        missing_optional = [field for field in optional_fields if field not in account_data]
                        if missing_optional:
                            logger.warning(f'[Kiro] Account {acc.id} (description: {acc.description}) missing optional fields: {missing_optional}')

                        # 验证expiresAt字段（如果存在）
                        if 'expiresAt' in account_data:
                            try:
                                datetime.fromisoformat(account_data['expiresAt'].replace('Z', '+00:00'))
                            except Exception as e:
                                logger.warning(f'[Kiro] Account {acc.id} has invalid expiresAt: {e}')

                        accounts_list.append(account_data)
                        logger.info(f'[Kiro] Successfully loaded account {acc.id}: {acc.description}')
                    except json.JSONDecodeError as e:
                        logger.error(f'[Kiro] Failed to parse account {acc.id} (description: {acc.description}): {e}')
                        logger.error(f'[Kiro] Account {acc.id} raw data: {acc.account[:200] if acc.account else "None"}...')
                        continue
                    except Exception as e:
                        logger.error(f'[Kiro] Unexpected error processing account {acc.id}: {e}')
                        continue

                logger.info(f'[Kiro] Loaded {len(accounts_list)} valid accounts from database')
                return accounts_list
            except Exception as e:
                logger.error(f'[Kiro] Failed to load accounts from database: {e}')
                return []
    except Exception as e:
        logger.error(f'[Kiro] Failed to load accounts from database: {e}')
        return []


async def load_proxy_from_db() -> Optional[str]:
    """从数据库加载代理配置"""
    try:
        async for db in get_db():
            try:
                # 获取第一条状态为1的代理记录
                stmt = select(Proxy).filter(Proxy.status == '1')
                result = await db.execute(stmt)
                proxy = result.scalars().first()

                if proxy:
                    # 构建完整的代理URL
                    proxy_url = f"{proxy.proxy_type}://"
                    if proxy.username and proxy.password:
                        proxy_url += f"{proxy.username}:{proxy.password}@"
                    proxy_url += f"{proxy.proxy_url}"
                    if proxy.proxy_port:
                        proxy_url += f":{proxy.proxy_port}"

                    logger.info(f'[Kiro] Loaded proxy from database: {proxy_url}')
                    return proxy_url
                else:
                    logger.info('[Kiro] No proxy configured in database')
                    return None
            except Exception as e:
                logger.error(f'[Kiro] Failed to load proxy from database: {e}')
                return None
    except Exception as e:
        logger.error(f'[Kiro] Failed to load proxy from database: {e}')
        return None


async def disable_account_in_db(account_id: int) -> bool:
    """将账号的状态设置为0（禁用）"""
    try:
        async for db in get_db():
            try:
                # 更新账号状态为0
                stmt = select(Account).filter(Account.id == account_id)
                result = await db.execute(stmt)
                account = result.scalars().first()

                if account:
                    account.status = '0'
                    await db.commit()
                    logger.info(f'[Kiro] Disabled account {account_id}: {account.description}')
                    return True
                else:
                    logger.warning(f'[Kiro] Account {account_id} not found in database')
                    return False
            except Exception as e:
                logger.error(f'[Kiro] Failed to disable account: {e}')
                return False
    except Exception as e:
        logger.error(f'[Kiro] Failed to disable account: {e}')
        return False
