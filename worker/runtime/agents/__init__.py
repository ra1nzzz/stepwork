"""对外 Agent 协议客户端（PRD §8.8 Agent 互操作的「出站」方向）。

已有的 ``mcp/server.py`` 是**入站**：外部 Agent 通过 MCP 调用 STEPWORK。
本包是**出站**：STEPWORK 作为客户端去连别人的 Server（PRD-AGT-004
「可连接外部搜索或知识库 Server」）。

两个方向共用 ``agent_connections`` / ``agent_tasks`` / ``agent_artifacts``
三张表与同一套信任等级 —— 外部拿回来的东西一律
``external-unverified`` + ``pending_review``（PRD-AGT-003）。
"""
