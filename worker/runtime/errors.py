"""错误词汇表：跨 handler / bus / agent 通道共享的领域异常。

**为什么单独一份**（dimension C 归档里的 P1）

此前 ``DispatchError`` 定义在 :mod:`worker.runtime.commands.bus` 里，被 40+
个 handler 反向 ``from worker.runtime.commands.bus import DispatchError`` 使用。
路由层因此同时扮演三个角色：错误词汇表 + 安全策略 + 审批业务；任何想抛一个
干净的领域错误的模块（哪怕与命令总线毫无关系，比如 provider 适配器）都
必须"顺手 import 一下 bus"，而 bus 又向下依赖具体业务，形成隐性的
**路由层 ⇄ 业务层双向依赖**。

搬到本模块后：

- ``errors.py`` 谁都可以引用，不引入任何业务耦合；
- ``bus`` 从这里导入并**重导出**（``DispatchError`` 名字保持不变），
  既有 40+ 处 ``from worker.runtime.commands.bus import DispatchError``
  不用一次性全改，兼容过渡；
- 新代码请直接从 ``worker.runtime.errors`` 引入，让"命令总线的错误"
  与"worker 领域的错误"重新解耦。
"""

from __future__ import annotations

__all__ = ["DispatchError"]


class DispatchError(Exception):
    """handler 内抛出的领域错误（转为 ``CommandResult.ok=False``）。

    Attributes:
        code: 稳定的错误码（如 ``INVALID_ARGUMENT`` / ``UNAVAILABLE`` /
            ``FORBIDDEN``）；前端与 CLI 用它做**分类判定**，不要用 message
            猜语义。
        message: 面向用户的人类可读信息，可含具体上下文（文件名、缺少的字段等），
            但**不该**含 token / api_key 之类凭据。
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")
