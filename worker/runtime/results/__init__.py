"""命令**响应**契约（``CommandResult.detail`` 的形状）。

请求方向早有单一事实源：``schemas/command-envelope.schema.json`` 锁住
bus / schema / types.ts 三处一致，加错命令立刻红。**响应方向此前完全裸奔** ——
两端都是 ``Any`` / ``unknown``，前端 46 处裸类型断言消费 detail。后端把
``detail.dedup`` 改个名，前端拿到 ``undefined``，没有编译错误、没有测试变红，
功能就此静默失效。

这不是假想：本仓实际发生过两次 ——

- ``source_assets.media_meta`` 实际列名是 ``metadata``，运行时才炸；
- transcript 的 ``content`` 被当 JSON 解析（实际是纯文本，分段在
  ``producer.segments``），解析失败被 except 吞掉，剪辑时间线的 marker
  永远为空，而测试恰好按同样的错误假设造数据，于是全绿。

**没有契约时，测试会和实现一起错。**

本包用 pydantic 模型定义每条命令的 detail，并在 dispatch 出口**真正校验**：

- 生产：不匹配只记 error 日志，绝不因契约问题打断用户的正常操作；
- 测试：``strict_results()`` 上下文里直接抛错，让漂移当场变红。

这条「测试严格、生产宽容」的分界是刻意的：契约的价值在于开发期挡住漂移，
而不是在用户机器上制造新的失败模式。
"""

from __future__ import annotations

from worker.runtime.results.registry import (
    RESULT_MODELS,
    ResultContractError,
    export_json_schemas,
    result_model_for,
    strict_results,
    validate_detail,
)

__all__ = [
    "RESULT_MODELS",
    "ResultContractError",
    "export_json_schemas",
    "result_model_for",
    "strict_results",
    "validate_detail",
]
