import aiohttp
import asyncio
import sys
import urllib3

# -------------------------- 全局配置（仅改代理，其余不变） --------------------------
PROXY = "127.0.0.1:10809"  # 你的HTTP代理【IP:端口】，直接替换即可
# PROXY = "205.185.120.206:25565"  # 你的HTTP代理【IP:端口】，直接替换即可
AMAZON_URL = "https://codewhisperer.us-east-1.amazonaws.com"

# 禁用SSL证书警告（亚马逊HTTPS站点必备）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 强化版浏览器请求头（规避亚马逊风控，完全不变）
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1"
}

# -------------------------- 核心异步请求函数（仅改代理配置，其余完全不变） --------------------------
async def amazon_proxy_request():
    print(f"🔍 正在通过 HTTP 代理 {PROXY} 访问亚马逊...")
    # ✅ 核心改动1：HTTP代理专用配置（aiohttp原生支持，无需额外依赖）
    proxy_url = f"http://{PROXY}"
    
    try:
        # ✅ 核心改动2：直接在ClientSession中配置proxy，无需连接器，极简写法
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url=AMAZON_URL,
                headers=HEADERS,
                proxy=proxy_url,          # 配置HTTP代理（核心）
                timeout=aiohttp.ClientTimeout(total=30),  # 30秒超时，适配代理
                ssl=False                 # 关闭SSL校验，解决亚马逊证书报错
            ) as response:
                # 状态码判断（200/301/302均为访问成功，亚马逊跳转属正常）
                if response.status in [200, 301, 302]:
                    print("✅ 亚马逊代理访问成功！✅")
                    print(f"📌 响应状态码：{response.status}")
                else:
                    print(f"⚠️  状态码异常：{response.status}（代理链路已通）")
                    print(f"📋 响应头信息：{dict(response.headers)}")

                # ✅ 打印亚马逊页面响应内容（前1000字符，不刷屏）
                html_content = await response.text()
                print("\n📄 亚马逊页面内容预览（前1000字符）：")
                print(html_content[:1000])
                print("=" * 80)

    except Exception as e:
        print(f"\n❌ 请求异常：{str(e)}")
        print("\n💡 快速排查建议：")
        print("  1. 确认代理软件已启动 + HTTP代理功能开启")
        print("  2. 核对HTTP代理端口是否为 {PROXY}（软件内查看）")
        print("  3. 切换【美国节点】后重试（亚马逊强制要求海外IP）")
        print("  4. 若有账号密码，按格式修改：http://账号:密码@IP:端口")

# -------------------------- 程序入口（兼容Windows/macOS/Linux，完全不变） --------------------------
if __name__ == "__main__":
    # Windows系统异步事件循环专属修复（必加，防止报错）
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    # 启动异步主函数
    asyncio.run(amazon_proxy_request())