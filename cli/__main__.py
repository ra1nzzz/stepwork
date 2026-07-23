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
    project_id = getattr(args, "project_id", None) or None
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
    """CLI 入口。返回进程退出码（0 成功 / 失败，异常为 1）。"""
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
