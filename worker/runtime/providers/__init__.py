"""Provider 层（W3 ASR / W4 AI）。

各 Provider 均为协议 + 实现分离；cloud 实现**零硬编码密钥**，
密钥仅来自环境变量 / Tauri 安全存储（三角色头脑风暴 P0）。
"""
