#!/usr/bin/env python3
"""
脚本核心功能: 模拟微信小程序，订阅并接收云端猫姿态推理结果
主要逻辑: 
1. 作为 WebSocket 客户端连接到推理脚本的发布端口（默认 4535）。
2. 持续监听服务端推送的消息。
3. 将收到的 JSON 字符串解析为字典，并格式化打印到终端，方便人工核对。
"""

import argparse
import asyncio
import json
import logging
from datetime import datetime

import websockets

# 配置日志输出格式，保持与服务端风格一致
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("MockMiniProgram")


class ResultSubscriber:
    """
    核心功能: 订阅并处理推理结果的客户端类
    """
    def __init__(self, server_ip: str, server_port: int, reconnect_delay: float = 3.0):
        """
        初始化参数:
            - server_ip: 推理服务端的 IP 地址
            - server_port: 推理服务端发布结果的端口（需与 cat_predict.py 的 --publish-port 对应）
            - reconnect_delay: 断线重连等待时间（秒）
        """
        self.ws_url = f"ws://{server_ip}:{server_port}"
        self.reconnect_delay = reconnect_delay

    def _format_confidence(self, details: dict) -> str:
        """辅助方法：将详细的置信度字典格式化为易读的字符串"""
        if not details:
            return "无数据"
        # 按置信度从高到低排序
        sorted_items = sorted(details.items(), key=lambda kv: kv[1], reverse=True)
        return ", ".join([f"{k}: {v:.1%}" for k, v in sorted_items])

    async def run(self):
        """
        核心功能: 持续连接服务端并接收消息的主循环。
        重要逻辑步骤:
            1. 尝试连接 WebSocket 服务端。
            2. 使用 async for 持续接收文本消息。
            3. 尝试将文本解析为 JSON 格式。
            4. 提取出 timestamp, behaviour, confidence 等关键变量并高亮打印。
            5. 处理网络异常并在断开时自动重连。
        """
        while True:
            try:
                logger.info(f"正在尝试连接到推理结果发布服务: {self.ws_url}")
                
                # 建立 WebSocket 连接
                async with websockets.connect(self.ws_url) as ws:
                    logger.info("✅ 成功连接！正在等待接收推理结果...\n" + "-"*50)
                    
                    # 持续监听收到的消息
                    async for message in ws:
                        try:
                            # 尝试解析 JSON 数据
                            data = json.loads(message)
                            
                            # 提取关键信息
                            msg_type = data.get("type", "unknown")
                            if msg_type != "inference_result":
                                logger.warning(f"收到未知类型的消息: {data}")
                                continue
                                
                            timestamp = data.get("timestamp", 0)
                            dt_str = datetime.fromtimestamp(timestamp).strftime('%H:%M:%S.%f')[:-3]
                            behaviour = data.get("behaviour", "Unknown")
                            confidence = data.get("confidence", 0.0)
                            details = data.get("details", {})
                            
                            # 格式化打印结果
                            detail_str = self._format_confidence(details)
                            print(f"[{dt_str}] 🐱 预测动作: \033[1;32m{behaviour:<8}\033[0m | 置信度: {confidence:.2f} | 详细: [{detail_str}]")
                            
                        except json.JSONDecodeError:
                            logger.error(f"解析 JSON 失败，收到原始内容: {message}")
                            
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"❌ 连接被服务端关闭 (code: {e.code}). {self.reconnect_delay}秒后尝试重连...")
            except ConnectionRefusedError:
                logger.warning(f"❌ 无法连接到服务器 (服务可能未启动). {self.reconnect_delay}秒后尝试重连...")
            except Exception as e:
                logger.error(f"⚠️ 发生未知错误: {e}. {self.reconnect_delay}秒后尝试重连...")

            # 遇到异常断开后，等待指定时间再进行下一轮 while 循环重连
            await asyncio.sleep(self.reconnect_delay)


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="模拟微信小程序接收推理结果")
    parser.add_argument("--ip", type=str, default="8.156.34.152", help="推理服务端的 IP 地址")
    parser.add_argument("--port", type=int, default=4535, help="推理服务端的发布端口 (默认 4535)")
    return parser.parse_args()


def main():
    args = parse_args()
    subscriber = ResultSubscriber(server_ip=args.ip, server_port=args.port)
    
    try:
        # 启动异步事件循环
        asyncio.run(subscriber.run())
    except KeyboardInterrupt:
        logger.info("\n⏹️ 用户手动终止测试脚本。")


if __name__ == "__main__":
    main()