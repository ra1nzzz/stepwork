"""STEPWORK 命令行入口（W7 Phase 3）。

``python -m cli`` —— 通过 Command Bus 与 worker 后端交互。

所有子命令统一走：构造信封（``source="cli"``、``actor.type="desktop"``）
→ ``asyncio.run(run_command(env))`` → 美化打印结果 JSON 到 stdout。

密钥安全：``config set`` 只能经 ``--file`` / ``--stdin`` 传入完整配置对象，
CLI 永不接收明文密钥参数，也绝不回显密钥明文。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import os
import sys
from typing import Any

from cli.config import add_config_subcommands, config_payload
from worker.runtime.app import build_envelope, run_command

# 本协议适配器的固定身份（schemas/command-envelope.schema.json：source=cli）。
SOURCE = "cli"
ACTOR_TYPE = "desktop"
DEFAULT_WORKSPACE_ID = "ws-local"


def build_parser() -> argparse.ArgumentParser:
    """构造顶层 ``ArgumentParser`` 与全部子命令。"""
    parser = argparse.ArgumentParser(
        prog="python -m cli",
        description="STEPWORK 命令行（经 Command Bus 调用 worker）",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="可选：worker SQLite 数据库路径（默认使用 worker 内置路径）",
    )
    parser.add_argument(
        "--project-id",
        default=None,
        help="可选：目标项目 id（部分命令在 project 作用域生效）",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # ----- config -----
    add_config_subcommands(sub)

    # ----- analyze -----
    an = sub.add_parser("analyze", help="分析源素材（AnalyzeSource）")
    an.set_defaults(command_type="AnalyzeSource")
    an.add_argument(
        "--source-id",
        dest="source_id",
        help="转写版（content_version）id → payload.transcript_version_id",
    )
    an.add_argument("--text", help="直接传入待分析的文本")
    an.add_argument("--brand", help="可选：品牌档 id")
    an.add_argument(
        "--provider",
        help="可选：per-request provider 提示，JSON 字符串（如 '{\"name\":\"cloud\"}'）",
    )

    # ----- topic -----
    topic = sub.add_parser("topic", help="选题相关命令")
    topic_sub = topic.add_subparsers(dest="topic_action", required=True)
    tg = topic_sub.add_parser("generate", help="生成选题角度（GenerateTopic）")
    tg.set_defaults(command_type="GenerateTopic")
    tg.add_argument(
        "--source-version-id",
        required=True,
        help="源 content_version id（transcript / script 等）",
    )
    tg.add_argument("--count", type=int, default=5, help="生成角度数量（默认 5）")
    tg.add_argument(
        "--provider",
        help="可选：provider 提示，JSON 字符串",
    )

    # ----- script -----
    script = sub.add_parser("script", help="脚本相关命令")
    script_sub = script.add_subparsers(dest="script_action", required=True)

    sg = script_sub.add_parser("generate", help="生成脚本（GenerateScript）")
    sg.set_defaults(command_type="GenerateScript")
    sg.add_argument("--proposal-version-id", help="选题提案版 id")
    sg.add_argument("--topic-id", help="指定角度 id")
    sg.add_argument("--outline", help="可选：提纲文本")
    sg.add_argument("--style", default="short_video", help="脚本风格（默认 short_video）")
    sg.add_argument("--provider", help="可选：provider 提示，JSON 字符串")

    ss = script_sub.add_parser("save", help="保存脚本（SaveScript）")
    ss.set_defaults(command_type="SaveScript")
    ss.add_argument(
        "--content",
        help="脚本正文（也可用 --file / --stdin 从文件或标准输入读取）",
    )
    ss.add_argument("--parent-version-id", help="可选：父版本 id（版本链）")
    src = ss.add_mutually_exclusive_group()
    src.add_argument("--file", metavar="PATH", help="从文件读取脚本正文")
    src.add_argument("--stdin", action="store_true", help="从标准输入读取脚本正文")

    # ----- import -----
    # 对齐桌面端 useImportStore：payload = {local_uri, kind, metadata}，
    # kind 由 MIME 推断（audio/* → audio、video/* → video、其余 document）。
    imp = sub.add_parser("import", help="导入源素材（ImportSource）")
    imp.set_defaults(command_type="ImportSource")
    imp.add_argument(
        "--project",
        dest="project",
        default=None,
        help="可选：目标项目 id（缺省回退到全局 --project-id 或默认项目）",
    )
    imp.add_argument(
        "--file",
        metavar="PATH",
        required=True,
        help="素材文件路径（写入 payload.local_uri，绝对路径）",
    )

    # ----- transcribe -----
    tr = sub.add_parser("transcribe", help="转写素材（TranscribeSource）")
    tr.set_defaults(command_type="TranscribeSource")
    tr.add_argument(
        "--asset-id",
        dest="asset_id",
        required=True,
        help="source_assets id → payload.asset_id",
    )

    # ----- render -----
    rd = sub.add_parser("render", help="渲染视频草稿（CreateRenderJob）")
    rd.set_defaults(command_type="CreateRenderJob")
    rd.add_argument(
        "--version-id",
        dest="version_id",
        required=True,
        help="源 content_version id → payload.source_version_id",
    )
    rd.add_argument(
        "--template",
        default="vertical-caption-v1",
        help="渲染模板（默认 vertical-caption-v1，与 worker RenderSpec 缺省一致）",
    )

    # ----- job -----
    job = sub.add_parser("job", help="任务查询命令")
    job_sub = job.add_subparsers(dest="job_action", required=True)

    js = job_sub.add_parser("status", help="查询任务状态（GetJobStatus）")
    js.set_defaults(command_type="GetJobStatus")
    js.add_argument("job_id", help="任务 id")

    jl = job_sub.add_parser("list", help="列出任务（ListJobs）")
    jl.set_defaults(command_type="ListJobs")
    jl.add_argument(
        "--state",
        dest="states",
        action="append",
        metavar="STATE",
        help="可选：按状态过滤（可重复；小写 JobState 值，如 running / failed）",
    )
    jl.add_argument(
        "--limit",
        type=int,
        default=None,
        help="可选：最多返回条数（缺省由 worker 决定）",
    )

    # ----- project -----
    proj = sub.add_parser("project", help="项目查询命令")
    proj_sub = proj.add_subparsers(dest="project_action", required=True)

    pl = proj_sub.add_parser("list", help="列出当前工作区项目（ListProjects）")
    pl.set_defaults(command_type="ListProjects")

    pg = proj_sub.add_parser("get", help="按 id 取单个项目（GetProject）")
    pg.set_defaults(command_type="GetProject")
    pg.add_argument("project_id", help="项目 id")

    return parser


def _parse_provider(value: str | None) -> dict[str, Any] | None:
    """把 ``--provider`` 的 JSON 字符串解析为 dict；空值返回 None。"""
    if not value:
        return None
    try:
        data: Any = json.loads(value)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid --provider JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("--provider must be a JSON object")
    return data


def _kind_from_mime(mime: str) -> str:
    """由 MIME 类型推断素材 kind（对齐桌面端 ``kindFromMime``）。"""
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    return "document"


def _import_payload(file_path: str) -> dict[str, Any]:
    """构造 ``ImportSource`` payload（对齐 useImportStore 发送的形状）。

    Raises:
        ValueError: 文件不存在。
    """
    if not os.path.isfile(file_path):
        raise ValueError(f"import file not found: {file_path}")
    abs_path = os.path.abspath(file_path)
    mime = mimetypes.guess_type(abs_path)[0] or "application/octet-stream"
    return {
        "local_uri": abs_path,
        "kind": _kind_from_mime(mime),
        "metadata": {
            "name": os.path.basename(abs_path),
            "size_bytes": os.path.getsize(abs_path),
            "mime_type": mime,
        },
    }


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    """根据子命令把解析后的参数映射为命令 payload。"""
    command = getattr(args, "command", None)

    if command == "config":
        return config_payload(args)

    if command == "analyze":
        payload: dict[str, Any] = {}
        if args.source_id:
            payload["transcript_version_id"] = args.source_id
        if args.text:
            payload["text"] = args.text
        if args.brand:
            payload["brand"] = args.brand
        provider = _parse_provider(getattr(args, "provider", None))
        if provider is not None:
            payload["provider"] = provider
        return payload

    if command == "topic":
        payload = {
            "source_version_id": args.source_version_id,
            "count": args.count,
        }
        provider = _parse_provider(getattr(args, "provider", None))
        if provider is not None:
            payload["provider"] = provider
        return payload

    if command == "script":
        action = getattr(args, "script_action", None)
        if action == "generate":
            payload = {
                "proposal_version_id": getattr(args, "proposal_version_id", None),
                "topic_id": getattr(args, "topic_id", None),
                "outline": getattr(args, "outline", None),
                "style": getattr(args, "style", "short_video"),
            }
            provider = _parse_provider(getattr(args, "provider", None))
            if provider is not None:
                payload["provider"] = provider
            return payload
        if action == "save":
            content = getattr(args, "content", None)
            if getattr(args, "stdin", False):
                content = sys.stdin.read()
            elif getattr(args, "file", None):
                with open(args.file, encoding="utf-8") as f:
                    content = f.read()
            if not content:
                raise ValueError("script save requires --content, --file, or --stdin")
            return {
                "content": content,
                "parent_version_id": getattr(args, "parent_version_id", None),
            }
        raise ValueError(f"unknown script action: {action!r}")

    if command == "import":
        return _import_payload(args.file)

    if command == "transcribe":
        # 对齐桌面端 useTranscriptStore：opts 缺省为空对象
        return {"asset_id": args.asset_id, "opts": {}}

    if command == "render":
        # 其余 RenderSpec 字段（tts_engine / resolution / fps）由 worker 缺省补齐
        return {
            "source_version_id": args.version_id,
            "template": args.template,
        }

    if command == "job":
        action = getattr(args, "job_action", None)
        if action == "status":
            return {"job_id": args.job_id}
        if action == "list":
            # 契约：states / limit 均可选，缺省不写入 payload
            payload = {}
            if getattr(args, "states", None):
                payload["states"] = args.states
            if getattr(args, "limit", None) is not None:
                payload["limit"] = args.limit
            return payload
        raise ValueError(f"unknown job action: {action!r}")

    if command == "project":
        action = getattr(args, "project_action", None)
        if action == "list":
            return {}
        if action == "get":
            return {"project_id": args.project_id}
        raise ValueError(f"unknown project action: {action!r}")

    raise ValueError(f"unknown command: {command!r}")


def build_envelope_for(
    args: argparse.Namespace,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> dict[str, Any]:
    """用 worker 的 ``build_envelope`` 构造命令信封。"""
    command_type = getattr(args, "command_type", None)
    if not command_type:
        raise ValueError("subcommand did not set command_type")
    payload = build_payload(args)
    # 子命令级 --project（如 import）优先于全局 --project-id
    project_id = (
        getattr(args, "project", None) or getattr(args, "project_id", None) or None
    )
    return build_envelope(
        command_type=command_type,
        source=SOURCE,
        actor_type=ACTOR_TYPE,
        workspace_id=workspace_id,
        project_id=project_id,
        idempotency_key=None,
        payload=payload,
    )


async def _dispatch(env: dict[str, Any], *, db_path: str | None) -> dict[str, Any]:
    """调用 worker Command Bus 并返 CommandResult dict。"""
    return await run_command(env, db_path=db_path)


def _print_error(message: str) -> None:
    """向 stdout 输出统一的错误信封（不回显任何密钥明文）。"""
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。返回进程退出码。

    退出码约定（脚本 / agent 可依赖）：
    - ``0``：命令执行成功（``result.ok == True``）。
    - ``1``：命令执行失败（``result.ok == False`` 或 dispatch 异常）。
    - ``2``：参数 / 用法错误（argparse 解析失败或 payload 构造非法）。
    JSON 结果始终且仅打印到 stdout（argparse 的 usage 信息走 stderr）。
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        env = build_envelope_for(args)
    except ValueError as e:
        _print_error(f"CLI_ARGUMENT: {e}")
        return 2

    db_path = getattr(args, "db_path", None)
    try:
        result = asyncio.run(_dispatch(env, db_path=db_path))
    except Exception as e:  # noqa: BLE001 - 顶层兜底，避免向用户抛 traceback
        _print_error(f"CLI_DISPATCH: {e}")
        return 1

    # 结果一律美化输出；后端已对密钥做掩码，CLI 不额外回显明文。
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if bool(result.get("ok")) else 1


if __name__ == "__main__":
    sys.exit(main())
