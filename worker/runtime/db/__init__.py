"""数据层（W3-W4 Batch 0）。

封装 SQLite 连接、迁移执行与仓储（repositories）。
所有上层（Job 引擎、Command Bus、各 domain handler）只依赖本包与
``worker.runtime.models``，不直接触碰 SQL。
"""
