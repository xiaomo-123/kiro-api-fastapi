import asyncio
import aiohttp

async def test_proxy():
    proxy_url = "http://107.189.1.92:25565"
    target_url = "https://www.amazonaws.com"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                target_url,
                proxy=proxy_url,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                print(f"状态码: {response.status}")
                print(f"响应头: {dict(response.headers)}")
                
                # 读取前100个字符的响应内容
                content = await response.text()
                print(f"响应内容(前100字符): {content[:100]}")
                
    except aiohttp.ClientHttpProxyError as e:
        print(f"代理错误: {e}")
        print(f"错误状态码: {e.status}")
        print(f"错误消息: {e.message}")
    except aiohttp.ClientError as e:
        print(f"客户端错误: {e}")
    except Exception as e:
        print(f"未知错误: {type(e).__name__}: {e}")

if __name__ == "__main__":
    asyncio.run(test_proxy())
