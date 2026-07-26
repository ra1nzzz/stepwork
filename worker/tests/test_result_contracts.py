"""响应契约的不变量（与 test_command_registry 同性质，锁的是**响应**方向）。

请求方向早有三处一致性锁；响应方向此前完全没有。这个文件补上的是：

1. 已声明契约的域**必须全覆盖** —— 往 publish/agent 加命令却忘了写 detail
   模型，立刻红；
2. 生成物（TS 类型 + JSON Schema）与模型同步；
3. 校验真的会拦住不符的 detail（不是写了个 validate 但从不生效）。

第 3 条尤其重要：本仓吃过「装饰性代码」的亏 —— 哈希存了但发布时从不比对、
开关能勾但背后零计算。契约要是也只写不查，就是同一个坑。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from worker.runtime.commands.bus import _ROUTES
from worker.runtime.results import RESULT_MODELS, ResultContractError, strict_results
from worker.runtime.results.models import ResultModel
from worker.runtime.results.registry import validate_detail

_ROOT = Path(__file__).resolve().parents[2]

#: 已完成契约化的 handler 模块。这些模块下的**每条**命令都必须有 detail 模型。
#:
#: 契约按域分批推进，所以这里是白名单而不是「全部命令」；但白名单内一条都不
#: 能漏 —— 否则「这个域做完了」就是句空话。
_CONTRACTED_MODULES = (
    "worker.runtime.handlers.publish",
    "worker.runtime.handlers.agent",
    "worker.runtime.handlers.mcp_client",
    "worker.runtime.handlers.a2a",
    "worker.runtime.handlers.acp",
    "worker.runtime.handlers.export_timeline",
)


def test_contracted_domains_are_fully_covered() -> None:
    """已契约化的域里不能有漏网命令。"""
    expected = {
        command
        for command, module in _ROUTES.items()
        if module in _CONTRACTED_MODULES
    }
    missing = sorted(expected - set(RESULT_MODELS))
    assert not missing, (
        f"这些命令属于已契约化的域但没有 detail 模型："
        f"{missing} —— 请在 worker/runtime/results/models.py 补上"
    )


def test_no_contract_for_unrouted_commands() -> None:
    """契约表里不能有 bus 根本没有的命令（改名后留下的孤儿）。"""
    orphans = sorted(set(RESULT_MODELS) - set(_ROUTES))
    assert not orphans, f"契约登记了但 bus 无路由：{orphans}"


def test_all_models_forbid_extra_fields() -> None:
    """多返回字段和少返回一样危险，必须 extra=forbid。

    允许多余字段会让「后端偷偷多返回一个字段、前端悄悄依赖上」成为可能，
    而那个字段从来没进过契约，下次重构就没了。
    """
    for command_type, model in RESULT_MODELS.items():
        assert issubclass(model, ResultModel), command_type
        assert model.model_config.get("extra") == "forbid", command_type


# ---------------------------------------------------------------------------
# 校验真的生效（否则契约就是装饰）
# ---------------------------------------------------------------------------


def test_validation_rejects_missing_field() -> None:
    with strict_results(), pytest.raises(ResultContractError):
        validate_detail("DeleteAgentConnection", {})


def test_validation_rejects_extra_field() -> None:
    with strict_results(), pytest.raises(ResultContractError):
        validate_detail("DeleteAgentConnection", {"deleted": "x", "surprise": 1})


def test_validation_rejects_wrong_type() -> None:
    with strict_results(), pytest.raises(ResultContractError):
        validate_detail("FireDueSchedules", {"fired": [], "count": "不是数字"})


def test_validation_accepts_correct_detail() -> None:
    with strict_results():
        validate_detail("DeleteAgentConnection", {"deleted": "conn_1"})
        validate_detail("FireDueSchedules", {"fired": [], "count": 0})


def test_unregistered_command_is_passed_through() -> None:
    """未登记契约的命令按老样子放行，契约才能按域分批推进。"""
    with strict_results():
        validate_detail("ListProjects", {"anything": True})


def test_production_mode_logs_instead_of_raising(caplog: pytest.LogCaptureFixture) -> None:
    """生产侧只记日志。

    契约漂移是开发期问题，不该在用户机器上变成新的失败模式 —— 用户正在发布
    视频，不该因为某个字段名对不上就整条命令失败。
    """
    with strict_results(False), caplog.at_level("ERROR", logger="worker.runtime"):
        validate_detail("DeleteAgentConnection", {})
    assert any("result contract violation" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 生成物同步
# ---------------------------------------------------------------------------


def test_generated_artifacts_are_in_sync() -> None:
    """TS 类型与 JSON Schema 必须与模型同步。

    不同步意味着前端拿到的类型是旧的 —— 那正是契约要防的「静默错位」，
    只不过换了个地方发生。
    """
    result = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "gen_result_types.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=_ROOT,
    )
    assert result.returncode == 0, (
        f"生成物与模型不同步：\n{result.stdout}\n{result.stderr}"
    )


def test_every_model_has_a_json_schema_on_disk() -> None:
    for command_type in RESULT_MODELS:
        target = _ROOT / "schemas" / "results" / f"{command_type}.schema.json"
        assert target.is_file(), f"缺少 {target}"
        schema = json.loads(target.read_text(encoding="utf-8"))
        assert schema["title"] == f"{command_type}Detail"
        # additionalProperties=false 是 extra=forbid 的 JSON Schema 投影，
        # 跨语言消费方靠它拿到同样的严格性
        assert schema.get("additionalProperties") is False, command_type
