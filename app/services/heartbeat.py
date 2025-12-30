
import threading
import time
import requests
import logging
from fastapi import FastAPI
from app.config import settings

# 配置日志
logger = logging.getLogger(__name__)

class HeartbeatService:
    """心跳服务，用于定期发送心跳请求保活API"""

    def __init__(self, app=None):
        self.app = app
        self.heartbeat_thread = None
        self.stop_event = threading.Event()
        self.heartbeat_url = None
        self.interval = 5  # 默认5秒间隔

    def init_app(self, app: FastAPI):
        """初始化应用"""
        self.app = app
        # 从配置中获取心跳URL和间隔时间
        self.heartbeat_url = f"http://127.0.0.1:{settings.SERVER_PORT}/health"
        self.interval = 5
        logger.info(f"心跳服务初始化，URL: {self.heartbeat_url}, 间隔: {self.interval}秒")

        # 注册应用关闭时的处理函数
        @app.on_event("shutdown")
        async def cleanup():
            if self.heartbeat_thread and self.heartbeat_thread.is_alive():
                self.stop()

    def start(self):
        """启动心跳服务"""
        if self.heartbeat_thread and self.heartbeat_thread.is_alive():
            logger.warning("心跳服务已在运行中")
            return

        self.stop_event.clear()
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()
        logger.info(f"心跳服务已启动，间隔: {self.interval}秒")

    def stop(self):
        """停止心跳服务"""
        self.stop_event.set()
        if self.heartbeat_thread and self.heartbeat_thread.is_alive():
            self.heartbeat_thread.join(timeout=1)
        logger.info("心跳服务已停止")

    def _heartbeat_loop(self):
        """心跳循环"""
        # 等待应用完全启动
        logger.info("等待应用完全启动...")
        time.sleep(10)
        logger.info("应用启动完成，开始发送心跳")

        while not self.stop_event.is_set():
            try:
                self._send_heartbeat()
                # 使用可中断的等待
                self.stop_event.wait(self.interval)
            except Exception as e:
                logger.error(f"心跳服务异常: {str(e)}")
                # 出现异常时等待一段时间再重试
                self.stop_event.wait(min(self.interval, 10))

    def _send_heartbeat(self):
        """发送心跳请求"""
        try:
            logger.info(f"发送心跳请求到: {self.heartbeat_url}")
            # 增加超时时间到10秒，并分别设置连接和读取超时
            response = requests.get(
                self.heartbeat_url,
                timeout=(5, 10)  # (连接超时, 读取超时)
            )
            if response.status_code == 200:
                logger.info(f"心跳请求成功，响应: {response.text}")
            else:
                logger.warning(f"心跳请求失败，状态码: {response.status_code}, 响应: {response.text}")
        except requests.exceptions.ConnectTimeout as e:
            logger.error(f"心跳请求连接超时: {str(e)}, URL: {self.heartbeat_url}")
        except requests.exceptions.ReadTimeout as e:
            logger.error(f"心跳请求读取超时: {str(e)}, URL: {self.heartbeat_url}")
        except requests.exceptions.RequestException as e:
            logger.error(f"心跳请求异常: {str(e)}, URL: {self.heartbeat_url}")

# 创建全局心跳服务实例
heartbeat_service = HeartbeatService()
