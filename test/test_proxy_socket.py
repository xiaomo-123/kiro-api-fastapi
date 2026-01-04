import requests
import urllib3

# -------------------------- 全局配置（仅需改这里的端口号） --------------------------
# 1. 本地代理地址+端口（Clash默认7890，其他软件自行替换）
PROXY = "127.0.0.1:10808"
# 2. 亚马逊测试地址
AMAZON_URL = "https://www.amazon.com"

# -------------------------- 核心修复配置 --------------------------
# 1. 禁用SSL警告（必加，亚马逊HTTPS站点专用）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
# 2. ✅ 正确的代理配置（socks5h协议，解决FileNotFound核心问题）
proxies = {
    "http": f"socks5h://{PROXY}",
    "https": f"socks5h://{PROXY}"
}
# 3. 浏览器请求头（固定UA，无需安装fake_useragent，避免额外报错）
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

# -------------------------- 测试主程序 --------------------------
if __name__ == "__main__":
    print(f"🔍 正在通过代理 {PROXY} 测试访问亚马逊...")
    try:
        response = requests.get(
            url=AMAZON_URL,
            proxies=proxies,
            headers=headers,
            timeout=20,       # 延长超时时间，适配本地代理
            verify=False      # 关闭SSL校验，避免证书报错
        )
        # 状态码200/301/302均代表访问成功（亚马逊会跳转）
        if response.status_code in [200, 301, 302]:
            print("✅ 代理访问亚马逊 SUCCESS！状态码：", response.status_code)
            print("✅ 代理IP有效性验证通过！")
        else:
            print(f"⚠️  状态码异常：{response.status_code}，但代理连接已成功")
            print(f"📋 响应头: {dict(response.headers)}")
        print("\n✅ 【3. 页面内容预览】（前1000字符，避免刷屏）")
        print(response.text[:1000])  # 只打印前1000个字符，按需调整数字
        print("="*80)
    except Exception as e:
        print(f"❌ 代理请求异常：{str(e)}")
        print("\n💡 快速排查建议：1.检查代理软件是否启动 2.核对端口号 3.切换socks5h协议")