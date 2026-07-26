"""cli 包测试（W7 Phase 3，Tranche 1/2 扩展）。

验证 CLI → Command Bus 的接线（无需真实 DB）：
- ``config get`` 构造正确的 ``GetConfig`` 信封（source=cli / actor.type=desktop）。
- ``config set`` 解析器只暴露 ``--file`` / ``--stdin``，**绝不**接收明文密钥参数。
- 进程退出码反映 ``result.ok``（0 成功 / 1 失败 / 2 用法错误）。
- Tranche 1 子命令（import / transcribe / render / job / project）构造与
  worker handler / 契约一致的 payload。
- Tranche 2 子命令（brand / workspace / versions / publish / analysis）
  构造契约定义的 camelCase payload，可选键缺省不写入。
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
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


def _capture_run_command(
    monkeypatch: pytest.MonkeyPatch, result: dict[str, Any]
) -> dict[str, Any]:
    """monkeypatch run_command，返回捕获信封的容器。"""
    captured: dict[str, Any] = {}

    async def fake_run_command(
        raw: dict[str, Any], *, db_path: str | None = None
    ) -> dict[str, Any]:
        captured["env"] = raw
        return result

    monkeypatch.setattr(cli_mod, "run_command", fake_run_command)
    return captured


# ----- 退出码（Tranche 1：进程退出码必须反映 result.ok） -----


def test_exit_code_1_when_result_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture_run_command(monkeypatch, {"ok": False, "error": "NOT_FOUND: x"})
    assert main(["project", "list"]) == 1


def test_exit_code_0_when_result_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture_run_command(monkeypatch, {"ok": True, "detail": {}})
    assert main(["project", "list"]) == 0


def test_exit_code_2_on_usage_error() -> None:
    # argparse 用法错误：SystemExit(2)，usage 走 stderr、stdout 无 JSON 污染
    with pytest.raises(SystemExit) as ei:
        main(["render"])  # 缺 --version-id
    assert ei.value.code == 2


def test_stdout_is_pure_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _capture_run_command(
        monkeypatch, {"ok": True, "job_id": "j-1", "artifact_ids": ["a-1"]}
    )
    rc = main(["job", "status", "j-1"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert rc == 0
    # job_id / artifact_ids 必须从 CommandResult 原样透传到输出
    assert parsed["job_id"] == "j-1"
    assert parsed["artifact_ids"] == ["a-1"]


# ----- 新增子命令的信封 / payload 形状 -----


def test_import_builds_importsource_envelope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"fake-video-bytes")

    captured = _capture_run_command(monkeypatch, {"ok": True, "artifact_ids": ["a-1"]})
    rc = main(["import", "--project", "proj-1", "--file", str(f)])

    assert rc == 0
    env = captured["env"]
    assert env["commandType"] == "ImportSource"
    assert env["source"] == "cli"
    assert env["projectId"] == "proj-1"
    payload = env["payload"]
    assert payload["local_uri"] == str(f.resolve())
    assert payload["kind"] == "video"
    assert payload["metadata"]["name"] == "clip.mp4"
    assert payload["metadata"]["size_bytes"] == len(b"fake-video-bytes")
    assert payload["metadata"]["mime_type"].startswith("video/")


def test_import_missing_file_is_usage_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _capture_run_command(monkeypatch, {"ok": True})
    rc = main(["import", "--file", str(tmp_path / "nope.mp4")])
    assert rc == 2


def test_transcribe_builds_transcribesource_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_run_command(monkeypatch, {"ok": True, "job_id": "j-1"})
    rc = main(["transcribe", "--asset-id", "asset-1"])

    assert rc == 0
    env = captured["env"]
    assert env["commandType"] == "TranscribeSource"
    assert env["payload"] == {"asset_id": "asset-1", "opts": {}}


def test_render_builds_createrenderjob_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_run_command(monkeypatch, {"ok": True, "job_id": "j-2"})
    rc = main(["render", "--version-id", "cv-1"])

    assert rc == 0
    env = captured["env"]
    assert env["commandType"] == "CreateRenderJob"
    # 默认模板与 worker RenderSpec 缺省一致
    assert env["payload"] == {
        "source_version_id": "cv-1",
        "template": "vertical-caption-v1",
    }


def test_job_status_builds_getjobstatus_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_run_command(monkeypatch, {"ok": True})
    rc = main(["job", "status", "j-9"])

    assert rc == 0
    env = captured["env"]
    assert env["commandType"] == "GetJobStatus"
    assert env["payload"] == {"job_id": "j-9"}


def test_job_list_builds_listjobs_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_run_command(monkeypatch, {"ok": True})
    rc = main(
        ["job", "list", "--state", "running", "--state", "failed", "--limit", "10"]
    )

    assert rc == 0
    env = captured["env"]
    assert env["commandType"] == "ListJobs"
    assert env["payload"] == {"states": ["running", "failed"], "limit": 10}

    # 契约：states / limit 均可选，缺省不写入 payload
    main(["job", "list"])
    assert captured["env"]["payload"] == {}


def test_project_list_and_get_build_query_envelopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_run_command(monkeypatch, {"ok": True})

    rc = main(["project", "list"])
    assert rc == 0
    assert captured["env"]["commandType"] == "ListProjects"
    assert captured["env"]["payload"] == {}

    rc = main(["project", "get", "proj-7"])
    assert rc == 0
    env = captured["env"]
    assert env["commandType"] == "GetProject"
    assert env["payload"] == {"project_id": "proj-7"}


# ----- Tranche 2：brand / workspace / versions / publish / analysis -----


def test_brand_list_builds_listbrandprofiles_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_run_command(monkeypatch, {"ok": True, "detail": {}})
    rc = main(["brand", "list"])

    assert rc == 0
    env = captured["env"]
    assert env["commandType"] == "ListBrandProfiles"
    assert env["source"] == "cli"
    assert env["payload"] == {}


def test_brand_create_builds_full_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_run_command(monkeypatch, {"ok": True})
    rc = main(
        [
            "brand",
            "create",
            "--name",
            "极简厨房",
            "--tone",
            "轻松专业",
            "--positioning",
            "家庭快手菜",
            "--audience",
            "上班族",
            "--pillar",
            "10分钟晚餐",
            "--pillar",
            "厨具测评",
            "--banned",
            "绝绝子",
            "--banned",
            "家人们",
        ]
    )

    assert rc == 0
    env = captured["env"]
    assert env["commandType"] == "CreateBrandProfile"
    assert env["payload"] == {
        "name": "极简厨房",
        "positioning": "家庭快手菜",
        "audience": "上班族",
        "tone": "轻松专业",
        "contentPillars": ["10分钟晚餐", "厨具测评"],
        "bannedExpressions": ["绝绝子", "家人们"],
    }


def test_brand_create_omits_absent_optional_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_run_command(monkeypatch, {"ok": True})
    rc = main(["brand", "create", "--name", "只有名字"])

    assert rc == 0
    # 契约：可选字段缺省不写入 payload
    assert captured["env"]["payload"] == {"name": "只有名字"}


def test_brand_create_requires_name() -> None:
    with pytest.raises(SystemExit) as ei:
        main(["brand", "create", "--tone", "轻松"])
    assert ei.value.code == 2


def test_brand_set_project_builds_payload_and_allows_null_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_run_command(monkeypatch, {"ok": True})

    rc = main(["brand", "set-project", "--project", "proj-1", "--profile", "bp-1"])
    assert rc == 0
    env = captured["env"]
    assert env["commandType"] == "SetProjectBrandProfile"
    assert env["projectId"] == "proj-1"
    assert env["payload"] == {"projectId": "proj-1", "profileId": "bp-1"}

    # 缺省 --profile → profileId 显式为 null（解除关联）
    rc = main(["brand", "set-project", "--project", "proj-1"])
    assert rc == 0
    assert captured["env"]["payload"] == {"projectId": "proj-1", "profileId": None}


def test_workspace_commands_build_contract_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_run_command(monkeypatch, {"ok": True})

    rc = main(["workspace", "list"])
    assert rc == 0
    assert captured["env"]["commandType"] == "ListWorkspaces"
    # 契约：includeArchived 可选，缺省不写入
    assert captured["env"]["payload"] == {}

    rc = main(["workspace", "list", "--include-archived"])
    assert rc == 0
    assert captured["env"]["payload"] == {"includeArchived": True}

    rc = main(["workspace", "create", "我的工作区"])
    assert rc == 0
    assert captured["env"]["commandType"] == "CreateWorkspace"
    assert captured["env"]["payload"] == {"name": "我的工作区"}

    rc = main(["workspace", "rename", "ws-2", "--name", "新名字"])
    assert rc == 0
    assert captured["env"]["commandType"] == "RenameWorkspace"
    assert captured["env"]["payload"] == {"workspaceId": "ws-2", "name": "新名字"}

    rc = main(["workspace", "archive", "ws-2"])
    assert rc == 0
    assert captured["env"]["commandType"] == "ArchiveWorkspace"
    assert captured["env"]["payload"] == {"workspaceId": "ws-2"}


def test_versions_list_builds_listcontentversions_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_run_command(monkeypatch, {"ok": True})
    rc = main(
        [
            "versions",
            "list",
            "--project",
            "proj-1",
            "--content-type",
            "script",
            "--limit",
            "5",
        ]
    )

    assert rc == 0
    env = captured["env"]
    assert env["commandType"] == "ListContentVersions"
    assert env["projectId"] == "proj-1"
    assert env["payload"] == {
        "projectId": "proj-1",
        "contentType": "script",
        "limit": 5,
    }

    # 契约：contentType / limit 均可选，缺省不写入 payload
    main(["versions", "list", "--project", "proj-1"])
    assert captured["env"]["payload"] == {"projectId": "proj-1"}


def test_versions_get_builds_getcontentversion_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_run_command(monkeypatch, {"ok": True})
    rc = main(["versions", "get", "cv-42"])

    assert rc == 0
    env = captured["env"]
    assert env["commandType"] == "GetContentVersion"
    assert env["payload"] == {"versionId": "cv-42"}


def test_publish_variant_create_builds_contract_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_run_command(monkeypatch, {"ok": True})
    rc = main(
        [
            "publish",
            "variant-create",
            "--project",
            "proj-1",
            "--platform",
            "douyin",
            "--title",
            "三分钟学会",
            "--body",
            "正文内容",
            "--tag",
            "美食",
            "--tag",
            "教程",
        ]
    )

    assert rc == 0
    env = captured["env"]
    assert env["commandType"] == "CreatePlatformVariant"
    assert env["projectId"] == "proj-1"
    assert env["payload"] == {
        "projectId": "proj-1",
        "platform": "douyin",
        "title": "三分钟学会",
        "body": "正文内容",
        "tags": ["美食", "教程"],
    }


def test_publish_variant_create_defaults_tags_to_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_run_command(monkeypatch, {"ok": True})
    rc = main(
        [
            "publish",
            "variant-create",
            "--project",
            "proj-1",
            "--platform",
            "generic",
            "--title",
            "t",
            "--body",
            "b",
            "--video-version-id",
            "cv-7",
        ]
    )

    assert rc == 0
    payload = captured["env"]["payload"]
    # tags 契约为必填数组：无 --tag 时为空数组；videoVersionId 可选
    assert payload["tags"] == []
    assert payload["videoVersionId"] == "cv-7"


def test_publish_variant_create_rejects_unknown_platform() -> None:
    with pytest.raises(SystemExit) as ei:
        main(
            [
                "publish",
                "variant-create",
                "--project",
                "proj-1",
                "--platform",
                "bilibili",
                "--title",
                "t",
                "--body",
                "b",
            ]
        )
    assert ei.value.code == 2


def test_publish_variant_list_and_export_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_run_command(monkeypatch, {"ok": True})

    rc = main(["publish", "variant-list", "--project", "proj-1"])
    assert rc == 0
    assert captured["env"]["commandType"] == "ListPlatformVariants"
    assert captured["env"]["payload"] == {"projectId": "proj-1"}

    rc = main(["publish", "export-bundle", "pv-3"])
    assert rc == 0
    assert captured["env"]["commandType"] == "ExportBundle"
    assert captured["env"]["payload"] == {"variantId": "pv-3"}


def test_analysis_save_builds_saveanalysis_envelope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report = {"summary": "概要", "hook": "开头钩子", "structure": [], "risks": []}
    raw = json.dumps(report, ensure_ascii=False)
    f = tmp_path / "report.json"
    f.write_text(raw, encoding="utf-8")

    captured = _capture_run_command(monkeypatch, {"ok": True, "detail": {}})
    rc = main(
        [
            "analysis",
            "save",
            "--project",
            "proj-1",
            "--file",
            str(f),
            "--parent",
            "cv-1",
        ]
    )

    assert rc == 0
    env = captured["env"]
    assert env["commandType"] == "SaveAnalysis"
    assert env["projectId"] == "proj-1"
    # content 为报告 JSON 的原文字符串
    assert env["payload"] == {
        "content": raw,
        "projectId": "proj-1",
        "parentVersionId": "cv-1",
    }


def test_analysis_save_omits_absent_optional_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    f = tmp_path / "report.json"
    f.write_text('{"summary": "s"}', encoding="utf-8")

    captured = _capture_run_command(monkeypatch, {"ok": True})
    rc = main(["analysis", "save", "--file", str(f)])

    assert rc == 0
    assert captured["env"]["payload"] == {"content": '{"summary": "s"}'}


def test_analysis_save_rejects_bad_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _capture_run_command(monkeypatch, {"ok": True})

    # 文件不存在 → 用法错误（退出码 2）
    assert main(["analysis", "save", "--file", str(tmp_path / "nope.json")]) == 2

    # JSON 非法 → 用法错误
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert main(["analysis", "save", "--file", str(bad)]) == 2

    # 顶层不是对象 → 用法错误
    arr = tmp_path / "arr.json"
    arr.write_text("[1, 2]", encoding="utf-8")
    assert main(["analysis", "save", "--file", str(arr)]) == 2
