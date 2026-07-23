"""cli 包测试（W7 Phase 3）。

验证 CLI → Command Bus 的接线（无需真实 DB）：
- ``config get`` 构造正确的 ``GetConfig`` 信封（source=cli / actor.type=desktop）。
- ``config set`` 解析器只暴露 ``--file`` / ``--stdin``，**绝不**接收明文密钥参数。
"""

from __future__ import annotations

import argparse
import io
from typing import Any

import pytest

import cli.__main__ as cli_mod
from cli.__main__ import build_parser, main


def _subparser_choices(parser: argparse.ArgumentParser) -> dict[str, Any]:
    """取某 parser 上挂载的 subparsers 的 choices。"""
    assert parser._subparsers is not None
    for grp in parser._subparsers._group_actions:
        choices = grp.choices
        return choices if isinstance(choices, dict) else {}
    return {}


def _find_subparser(parser: argparse.ArgumentParser, *names: str) -> argparse.ArgumentParser:
    """沿子命令名链定位嵌套 subparser。"""
    node = parser
    for name in names:
        node = _subparser_choices(node)[name]
    return node


def test_config_get_builds_getconfig_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_run_command(
        raw: dict[str, Any], *, db_path: str | None = None
    ) -> dict[str, Any]:
        captured["env"] = raw
        return {"ok": True, "commandId": raw.get("commandId")}

    monkeypatch.setattr(cli_mod, "run_command", fake_run_command)
    rc = main(["config", "get"])

    assert rc == 0
    env = captured["env"]
    assert env["commandType"] == "GetConfig"
    assert env["source"] == "cli"
    assert isinstance(env["actor"], dict)
    assert env["actor"]["type"] == "desktop"
    assert env["payload"] == {}


def test_config_set_builds_updateconfig_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_run_command(
        raw: dict[str, Any], *, db_path: str | None = None
    ) -> dict[str, Any]:
        captured["env"] = raw
        return {"ok": True}

    monkeypatch.setattr(cli_mod, "run_command", fake_run_command)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"llm": {"model": "step-3.7"}}'))

    rc = main(["config", "set", "--stdin"])
    assert rc == 0

    env = captured["env"]
    assert env["commandType"] == "UpdateConfig"
    assert env["source"] == "cli"
    assert env["actor"]["type"] == "desktop"
    assert env["payload"] == {"llm": {"model": "step-3.7"}}


def test_config_set_exposes_file_and_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    set_parser = _find_subparser(build_parser(), "config", "set")

    option_strings: list[str] = []
    positionals: list[str] = []
    for action in set_parser._actions:
        option_strings.extend(action.option_strings)
        if isinstance(action, argparse._StoreAction) and not action.option_strings:
            positionals.append(str(action.dest))

    lowered = {opt.lower() for opt in option_strings}

    # 密钥类选项绝不应当作为 CLI 参数出现
    for forbidden in ("--api-key", "--secret", "--token", "--password", "--apikey"):
        assert forbidden not in lowered, f"config set 不得暴露密钥参数 {forbidden}"

    # 允许的配置来源：文件 / 标准输入
    assert "--file" in lowered
    assert "--stdin" in lowered

    # 不得有任何接收明文密钥的位置参数
    assert not positionals, f"config set 不得有位置参数 {positionals}"


def test_config_set_parses_file_and_stdin() -> None:
    parser = build_parser()
    a1 = parser.parse_args(["config", "set", "--file", "x.json"])
    assert a1.command_type == "UpdateConfig"
    assert a1.file == "x.json"

    a2 = parser.parse_args(["config", "set", "--stdin"])
    assert a2.command_type == "UpdateConfig"
    assert a2.stdin is True


def test_config_set_rejects_both_file_and_stdin() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["config", "set", "--file", "x.json", "--stdin"])
