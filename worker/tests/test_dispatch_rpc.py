"""``_dispatch`` / ``handle_command`` RPC 包装回归测试。

守卫 R3 之后发现的一个发布阻断 bug：``handle_command`` 正常路径返回的是
扁平 ``CommandResult`` 字典（``{ok, commandId, error}``），但 ``__main__._dispatch``
对 ``command.*`` 走 ``if "result" in payload`` 分支、否则把 ``payload["error"]``
当 ``{"code","message"}`` 字典调 ``.get``——而 ``error`` 实际是字符串/None，
导致 ``AttributeError`` 击垮 worker 主循环。所有 ``command.*`` / ``job.*`` RPC
在 ``db_conn`` 就绪后都会崩。pytest 此前只直接测 ``dispatch()``，从没走过
``_dispatch`` 这层，故漏网。

修复：``handle_command`` 正常路径改为 ``return {"result": await dispatch(...)}``，
与模块 docstring 合约一致。本文件即该回归守卫。
"""

from __future__ import annotations

from typing import Any

from worker.runtime.db.connection import in_memory
from worker.runtime.handlers import commands
from worker.runtime.state import WorkerState


def _state() -> WorkerState:
    s = WorkerState()
    s.db_conn = in_memory()  # 合法连接即可；崩在 parse_envelope，不碰库
    return s


def _env(**over: Any) -> dict[str, Any]:
    e = {
        "commandId": "c",
        "commandType": "GenerateTopic",
        "schemaVersion": "1",
        "actor": {"type": "desktop", "id": "ui"},
        "source": "desktop",
        "workspaceId": "w",
        "requestedAt": "t",
        "payload": {},
    }
    e.update(over)
    return e


async def test_handle_command_wraps_result_on_bad_actor() -> None:
    """非法 actor.type → 必须包成 {"result": CommandResult}，且不抛 AttributeError。"""
    s = _state()
    ret = await commands.handle_command(
        {"envelope": _env(actor={"type": "bot", "id": "ui"})}, s
    )
    assert "result" in ret, "handle_command 必须返回 {'result': ...} 包装"
    assert ret["result"]["ok"] is False
    assert "invalid actor.type" in ret["result"]["error"]


async def test_handle_command_wraps_result_on_bad_schema() -> None:
    """非法 schemaVersion → 同样必须包成 {"result": ...}，干净拒绝。"""
    s = _state()
    ret = await commands.handle_command(
        {"envelope": _env(schemaVersion="99")}, s
    )
    assert "result" in ret
    assert ret["result"]["ok"] is False
    assert "schemaVersion" in ret["result"]["error"]
