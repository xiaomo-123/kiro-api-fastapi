import asyncio
import aiohttp
from aiohttp import ClientTimeout

async def test_proxy():
    proxy_url = "http://107.189.1.92:25565"
    target_url = "https://www.amazonaws.com"
    
    try:
        # 1. 超时配置（10秒总超时，适配你的需求）
        timeout = ClientTimeout(total=10)
        
        # 2. 创建SSL上下文 → 核心：关闭SSL证书验证、忽略SSL警告
        ssl_context = aiohttp.TCPConnector(
            ssl=False,  # ✅ 完全忽略SSL证书验证（最关键配置）
            limit=100   # 连接池限制，不影响核心功能
        )

        # 3. 会话绑定SSL上下文+超时+代理
        async with aiohttp.ClientSession(connector=ssl_context, timeout=timeout) as session:
            async with session.get(target_url, proxy=proxy_url) as response:
                print(f"✅ 请求成功 | 状态码: {response.status}")
                content = await response.text()
                print(f"✅ 响应内容(前100字符): {content[:100]}")
                
    except aiohttp.ClientConnectorError as e:
        print(f"❌ 代理连接失败: {e}")
        print("⚠️  核心原因：代理IP/端口无效、端口未开放、代理服务已宕机")
    except aiohttp.ClientError as e:
        print(f"❌ 客户端错误: {e}")
    except Exception as e:
        print(f"❌ 未知错误: {type(e).__name__}: {e}")

if __name__ == "__main__":    
    asyncio.run(test_proxy())