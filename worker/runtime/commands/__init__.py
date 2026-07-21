"""Command Bus（W3-W4 Batch 0）。

- ``envelope``：命令信封校验（结构对齐 schemas/command-envelope.schema.json）。
- ``bus``：路由 ``commandType`` → 对应 handler（懒加载，避免循环依赖）。
"""
