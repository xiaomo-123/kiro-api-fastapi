import asyncio
import aiohttp
from aiohttp import ClientTimeout
import warnings

# 禁用SSL警告
warnings.filterwarnings('ignore', category=ResourceWarning)

async def test_proxy(proxy_url, target_url):
    print(f"\n🔍 测试代理连接...")
    print(f"📡 代理地址: {proxy_url}")
    print(f"🎯 目标URL: {target_url}")
    print("-" * 50)

    try:
        # 1. 超时配置
        timeout = ClientTimeout(
            total=30,      # 30秒总超时
            connect=10,    # 10秒连接超时
            sock_read=20   # 20秒读取超时
        )

        # 2. 创建SSL上下文 - 关闭SSL证书验证
        ssl_context = aiohttp.TCPConnector(
            ssl=False,              # 完全忽略SSL证书验证
            limit=100,              # 连接池限制
            force_close=True,       # 强制关闭连接
            enable_cleanup_closed=True  # 启用连接清理
        )

        # 3. 会话绑定SSL上下文+超时+代理
        async with aiohttp.ClientSession(connector=ssl_context, timeout=timeout) as session:
            print("📡 正在通过代理发送请求...")
            async with session.get(
                target_url,
                proxy=proxy_url,
                allow_redirects=False  # 禁用自动重定向
            ) as response:
                print(f"✅ 请求成功 | 状态码: {response.status}")
                print(f"📋 响应头: {dict(response.headers)}")
                content = await response.text()
                print(f"✅ 响应内容(前200字符): {content[:200]}")

    except aiohttp.ClientProxyConnectionError as e:
        print(f"❌ 代理连接失败: {e}")
        print("⚠️  可能原因：")
        print("   - 代理IP/端口无效")
        print("   - 端口未开放")
        print("   - 代理服务已宕机")
        print("   - 网络连接问题")
    except aiohttp.ClientHttpProxyError as e:
        print(f"❌ 代理HTTP错误: {e}")
        print(f"⚠️  状态码: {e.status}")
        print(f"⚠️  消息: {e.message}")
    except aiohttp.ClientConnectorError as e:
        print(f"❌ 连接器错误: {e}")
        print("⚠️  可能原因：")
        print("   - 目标服务器无法访问")
        print("   - DNS解析失败")
        print("   - 网络连接问题")
    except aiohttp.ClientError as e:
        print(f"❌ 客户端错误: {type(e).__name__}: {e}")
    except asyncio.TimeoutError:
        print(f"❌ 请求超时")
        print("⚠️  可能原因：")
        print("   - 代理响应慢")
        print("   - 网络延迟高")
        print("   - 目标服务器响应慢")
    except Exception as e:
        print(f"❌ 未知错误: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

async def test_all_proxies():
    # 三个代理地址
    proxies = [
        "http://209.141.44.111:25565",
        "http://107.189.1.92:25565",
        "http://107.189.1.233:25565"
    ]

    # 测试目标URL列表
    target_urls = [
        "https://www.google.com",
        "https://www.amazonaws.com"
    ]

    # 先测试所有代理访问谷歌，再测试所有代理访问亚马逊
    for target_url in target_urls:
        print(f"\n{'#'*60}")
        print(f"测试目标: {target_url}")
        print(f"{'#'*60}")
        for i, proxy in enumerate(proxies, 1):
            print(f"\n{'='*60}")
            print(f"测试代理 {i}/{len(proxies)}")
            print(f"{'='*60}")
            await test_proxy(proxy, target_url)

if __name__ == "__main__":
    asyncio.run(test_all_proxies())
