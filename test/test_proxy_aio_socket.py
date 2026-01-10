import aiohttp
import asyncio
import sys
from aiohttp_socks import ProxyConnector

# -------------------------- 全局配置（无需修改，直接用） --------------------------
PROXY = "127.0.0.1:10808"  # 你的本地SOCKS5代理端口
AMAZON_URL = "https://www.amazon.com"

# ✅ 强化版浏览器请求头（亚马逊风控规避必备）
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1"
}

# -------------------------- 核心异步函数（所有操作放入异步内） --------------------------
async def amazon_proxy_request():
    print(f"🔍 正在通过 SOCKS5 代理 {PROXY} 访问亚马逊...")
    # ✅ 核心修复：代理连接器【移到异步函数内部】创建（事件循环内执行）
    connector = ProxyConnector.from_url(f"socks5://{PROXY}")
    
    try:
        # 创建异步会话 + 绑定代理连接器
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                url=AMAZON_URL,
                headers=HEADERS,
                timeout=aiohttp.ClientTimeout(total=30),  # 30秒超时，适配代理网络
                ssl=False  # 关闭SSL校验，解决亚马逊证书报错
            ) as response:
                # 状态码判断（200/301/302均为访问成功，亚马逊会跳转）
                if response.status in [200, 301, 302]:
                    print("✅ 亚马逊代理访问成功！✅")
                    print(f"📌 响应状态码：{response.status}")
                else:
                    print(f"⚠️  状态码异常：{response.status}（代理链路已通）")
                    print(f"📋 响应头：{dict(response.headers)}")

                # ✅ 打印亚马逊页面响应内容（前1000字符，不刷屏）
                html_content = await response.text()
                print("\n📄 亚马逊页面内容预览（前1000字符）：")
                print(html_content[:1000])
                print("=" * 80)

    except Exception as e:
        print(f"\n❌ 请求异常：{str(e)}")
        print("\n💡 快速排查建议：")
        print("  1. 确认代理软件已启动 + SOCKS5功能开启")
        print("  2. 核对代理端口是否为 10808（软件内查看）")
        print("  3. 切换【美国节点】后重试（亚马逊强制要求海外IP）")

# -------------------------- 程序入口（兼容Windows/macOS/Linux） --------------------------
if __name__ == "__main__":
    # ✅ Windows系统异步事件循环专属修复（必加）
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    # ✅ 启动异步主函数，创建并运行事件循环
    asyncio.run(amazon_proxy_request())