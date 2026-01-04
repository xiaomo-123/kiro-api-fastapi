import requests
import warnings
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 禁用SSL警告
warnings.filterwarnings('ignore', category=ResourceWarning)
warnings.filterwarnings('ignore', message='Unverified HTTPS request')

def test_proxy(proxy_url, target_url):
    print(f"\n🔍 测试代理连接...")
    print(f"📡 代理地址: {proxy_url}")
    print(f"🎯 目标URL: {target_url}")
    print("-" * 50)

    try:
        # 1. 创建会话
        session = requests.Session()

        # 2. 配置重试策略
        retry_strategy = Retry(
            total=3,                      # 总重试次数
            backoff_factor=1,             # 重试间隔因子
            status_forcelist=[429, 500, 502, 503, 504],  # 需要重试的状态码
            allowed_methods=["HEAD", "GET", "OPTIONS"]  # 允许重试的HTTP方法
        )

        # 3. 创建适配器并应用重试策略
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=100,         # 连接池大小
            pool_maxsize=100
        )

        # 4. 将适配器挂载到会话
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        # 5. 配置代理
        proxies = {
            'http': proxy_url,
            'https': proxy_url
        }

        # 6. 发送请求
        print("📡 正在通过代理发送请求...")
        response = session.get(
            target_url,
            proxies=proxies,
            timeout=10,                   # 30秒超时
            verify=False,                 # 禁用SSL证书验证
            
        )

        print(f"✅ 请求成功 | 状态码: {response.status_code}")
        print(f"📋 响应头:")
        for key, value in response.headers.items():
            print(f"   {key}: {value}")
        print(f"✅ 响应内容(前200字符): {response.text[:200]}")

    except requests.exceptions.ProxyError as e:
        print(f"❌ 代理错误: {e}")
        print("⚠️  可能原因：")
        print("   - 代理IP/端口无效")
        print("   - 端口未开放")
        print("   - 代理服务已宕机")
        print("   - 代理需要认证")
    except requests.exceptions.ConnectTimeout as e:
        print(f"❌ 连接超时: {e}")
        print("⚠️  可能原因：")
        print("   - 代理响应慢")
        print("   - 网络延迟高")
        print("   - 目标服务器响应慢")
    except requests.exceptions.ConnectionError as e:
        print(f"❌ 连接错误: {e}")
        print("⚠️  可能原因：")
        print("   - 目标服务器无法访问")
        print("   - DNS解析失败")
        print("   - 网络连接问题")
    except requests.exceptions.Timeout as e:
        print(f"❌ 请求超时: {e}")
        print("⚠️  可能原因：")
        print("   - 请求处理时间过长")
        print("   - 网络不稳定")
    except requests.exceptions.RequestException as e:
        print(f"❌ 请求错误: {type(e).__name__}: {e}")
    except Exception as e:
        print(f"❌ 未知错误: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    # 三个代理地址
    proxies = [
        "http://127.0.0.1:10809"
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
            test_proxy(proxy, target_url)
