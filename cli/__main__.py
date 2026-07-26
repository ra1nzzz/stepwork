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
    parser.add_argument(
        "--workspace-id",
        dest="workspace_id",
        default=DEFAULT_WORKSPACE_ID,
        help=f"可选：目标工作区 id（默认 {DEFAULT_WORKSPACE_ID}）→ 信封 workspaceId",
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
    proj = sub.add_parser("project", help="项目命令")
    proj_sub = proj.add_subparsers(dest="project_action", required=True)

    pl = proj_sub.add_parser("list", help="列出当前工作区项目（ListProjects）")
    pl.set_defaults(command_type="ListProjects")

    pg = proj_sub.add_parser("get", help="按 id 取单个项目（GetProject）")
    pg.set_defaults(command_type="GetProject")
    pg.add_argument("project_id", help="项目 id")

    pc = proj_sub.add_parser("create", help="新建项目（CreateProject）")
    pc.set_defaults(command_type="CreateProject")
    pc.add_argument("--title", required=True, help="项目标题 → payload.title")

    # ----- brand（Tranche 2：BrandProfile） -----
    brand = sub.add_parser("brand", help="品牌档（BrandProfile）命令")
    brand_sub = brand.add_subparsers(dest="brand_action", required=True)

    bl = brand_sub.add_parser("list", help="列出品牌档（ListBrandProfiles）")
    bl.set_defaults(command_type="ListBrandProfiles")

    bc = brand_sub.add_parser("create", help="新建品牌档（CreateBrandProfile）")
    bc.set_defaults(command_type="CreateBrandProfile")
    bc.add_argument("--name", required=True, help="品牌档名称")
    bc.add_argument("--tone", help="可选：语气")
    bc.add_argument("--positioning", help="可选：定位")
    bc.add_argument("--audience", help="可选：受众")
    bc.add_argument(
        "--pillar",
        dest="pillars",
        action="append",
        metavar="PILLAR",
        help="可选：内容支柱（可重复）→ payload.contentPillars",
    )
    bc.add_argument(
        "--banned",
        dest="banned",
        action="append",
        metavar="EXPR",
        help="可选：禁用表达（可重复）→ payload.bannedExpressions",
    )

    bp = brand_sub.add_parser(
        "set-project", help="项目关联品牌档（SetProjectBrandProfile）"
    )
    bp.set_defaults(command_type="SetProjectBrandProfile")
    bp.add_argument("--project", required=True, help="项目 id → payload.projectId")
    bp.add_argument(
        "--profile",
        default=None,
        help="品牌档 id；缺省表示解除关联（payload.profileId = null）",
    )

    # ----- workspace（Tranche 2：PRD-WS-001） -----
    ws = sub.add_parser("workspace", help="工作区（Workspace）命令")
    ws_sub = ws.add_subparsers(dest="workspace_action", required=True)

    wl = ws_sub.add_parser(
        "list", help="列出工作区（ListWorkspaces，缺省不含已归档）"
    )
    wl.set_defaults(command_type="ListWorkspaces")
    wl.add_argument(
        "--include-archived",
        dest="include_archived",
        action="store_true",
        help="包含已归档的工作区 → payload.includeArchived",
    )

    wc = ws_sub.add_parser("create", help="新建工作区（CreateWorkspace）")
    wc.set_defaults(command_type="CreateWorkspace")
    wc.add_argument("name", help="工作区名称")

    wr = ws_sub.add_parser("rename", help="重命名工作区（RenameWorkspace）")
    wr.set_defaults(command_type="RenameWorkspace")
    wr.add_argument("workspace_id", help="工作区 id")
    wr.add_argument("--name", required=True, help="新名称")

    wa = ws_sub.add_parser("archive", help="归档工作区（ArchiveWorkspace）")
    wa.set_defaults(command_type="ArchiveWorkspace")
    wa.add_argument("workspace_id", help="工作区 id")

    # ----- versions（Tranche 2：内容版本查询） -----
    ver = sub.add_parser("versions", help="内容版本查询命令")
    ver_sub = ver.add_subparsers(dest="versions_action", required=True)

    vl = ver_sub.add_parser("list", help="列出内容版本（ListContentVersions）")
    vl.set_defaults(command_type="ListContentVersions")
    vl.add_argument("--project", required=True, help="项目 id → payload.projectId")
    vl.add_argument(
        "--content-type",
        dest="content_type",
        help="可选：按内容类型过滤（如 script / transcript / analysis）",
    )
    vl.add_argument(
        "--limit",
        type=int,
        default=None,
        help="可选：最多返回条数（缺省由 worker 决定，默认 20）",
    )

    vg = ver_sub.add_parser("get", help="取单个内容版本全文（GetContentVersion）")
    vg.set_defaults(command_type="GetContentVersion")
    vg.add_argument("version_id", help="content_version id")

    # ----- publish（Tranche 2：PRD-PUB-001/002） -----
    pub = sub.add_parser("publish", help="发布（平台变体 / 导出）命令")
    pub_sub = pub.add_subparsers(dest="publish_action", required=True)

    pvc = pub_sub.add_parser(
        "variant-create", help="创建平台变体（CreatePlatformVariant）"
    )
    pvc.set_defaults(command_type="CreatePlatformVariant")
    pvc.add_argument("--project", required=True, help="项目 id → payload.projectId")
    pvc.add_argument(
        "--platform",
        required=True,
        choices=["douyin", "generic"],
        help="目标平台",
    )
    pvc.add_argument("--title", required=True, help="变体标题")
    pvc.add_argument("--body", required=True, help="变体正文")
    pvc.add_argument(
        "--tag",
        dest="tags",
        action="append",
        metavar="TAG",
        help="标签（可重复）→ payload.tags",
    )
    pvc.add_argument(
        "--video-version-id",
        dest="video_version_id",
        help="可选：视频 content_version id → payload.videoVersionId",
    )

    pvl = pub_sub.add_parser(
        "variant-list", help="列出平台变体（ListPlatformVariants）"
    )
    pvl.set_defaults(command_type="ListPlatformVariants")
    pvl.add_argument("--project", required=True, help="项目 id → payload.projectId")

    pe = pub_sub.add_parser("export-bundle", help="导出发布包（ExportBundle）")
    pe.set_defaults(command_type="ExportBundle")
    pe.add_argument("variant_id", help="平台变体 id")

    # ----- analysis（Tranche 2：PRD-ANA-004） -----
    ana = sub.add_parser("analysis", help="分析报告命令")
    ana_sub = ana.add_subparsers(dest="analysis_action", required=True)

    asv = ana_sub.add_parser("save", help="保存分析报告为新版本（SaveAnalysis）")
    asv.set_defaults(command_type="SaveAnalysis")
    asv.add_argument("--project", help="可选：项目 id → payload.projectId")
    asv.add_argument(
        "--file",
        metavar="PATH",
        required=True,
        help="分析报告 JSON 文件路径（原文作为 payload.content）",
    )
    asv.add_argument(
        "--parent",
        dest="parent_version_id",
        help="可选：父版本 id → payload.parentVersionId",
    )

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


def _analysis_save_payload(args: argparse.Namespace) -> dict[str, Any]:
    """构造 ``SaveAnalysis`` payload：报告从 ``--file`` 读入。

    契约：``content`` 为报告的 JSON 字符串（原文透传），读取时校验其为
    合法 JSON 对象，避免把坏文件写进版本链。

    Raises:
        ValueError: 文件缺失 / JSON 非法 / 顶层不是对象。
    """
    path = args.file
    if not os.path.isfile(path):
        raise ValueError(f"analysis report file not found: {path}")
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid analysis report JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("analysis report must be a JSON object")

    payload: dict[str, Any] = {"content": raw}
    if getattr(args, "project", None):
        payload["projectId"] = args.project
    if getattr(args, "parent_version_id", None):
        payload["parentVersionId"] = args.parent_version_id
    return payload


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
        if action == "create":
            # 契约（worker/runtime/handlers/projects.py）：title 必填，
            # brandProfileId 可选（CLI 暂不暴露）
            return {"title": args.title}
        raise ValueError(f"unknown project action: {action!r}")

    if command == "brand":
        action = getattr(args, "brand_action", None)
        if action == "list":
            return {}
        if action == "create":
            # 契约（Tranche 2）：CreateBrandProfile 可选字段缺省不写入 payload
            payload = {"name": args.name}
            for key in ("positioning", "audience", "tone"):
                value = getattr(args, key, None)
                if value:
                    payload[key] = value
            if getattr(args, "pillars", None):
                payload["contentPillars"] = args.pillars
            if getattr(args, "banned", None):
                payload["bannedExpressions"] = args.banned
            return payload
        if action == "set-project":
            # profileId 允许为 null（解除项目与品牌档的关联）
            return {
                "projectId": args.project,
                "profileId": getattr(args, "profile", None),
            }
        raise ValueError(f"unknown brand action: {action!r}")

    if command == "workspace":
        action = getattr(args, "workspace_action", None)
        if action == "list":
            # includeArchived 可选，缺省不写入 payload
            if getattr(args, "include_archived", False):
                return {"includeArchived": True}
            return {}
        if action == "create":
            return {"name": args.name}
        if action == "rename":
            return {"workspaceId": args.workspace_id, "name": args.name}
        if action == "archive":
            return {"workspaceId": args.workspace_id}
        raise ValueError(f"unknown workspace action: {action!r}")

    if command == "versions":
        action = getattr(args, "versions_action", None)
        if action == "list":
            # 契约：contentType / limit 均可选，缺省不写入 payload
            payload = {"projectId": args.project}
            if getattr(args, "content_type", None):
                payload["contentType"] = args.content_type
            if getattr(args, "limit", None) is not None:
                payload["limit"] = args.limit
            return payload
        if action == "get":
            return {"versionId": args.version_id}
        raise ValueError(f"unknown versions action: {action!r}")

    if command == "publish":
        action = getattr(args, "publish_action", None)
        if action == "variant-create":
            payload = {
                "projectId": args.project,
                "platform": args.platform,
                "title": args.title,
                "body": args.body,
                # tags 契约为必填数组：无 --tag 时发送空数组
                "tags": getattr(args, "tags", None) or [],
            }
            if getattr(args, "video_version_id", None):
                payload["videoVersionId"] = args.video_version_id
            return payload
        if action == "variant-list":
            return {"projectId": args.project}
        if action == "export-bundle":
            return {"variantId": args.variant_id}
        raise ValueError(f"unknown publish action: {action!r}")

    if command == "analysis":
        action = getattr(args, "analysis_action", None)
        if action == "save":
            return _analysis_save_payload(args)
        raise ValueError(f"unknown analysis action: {action!r}")

    raise ValueError(f"unknown command: {command!r}")


def build_envelope_for(args: argparse.Namespace) -> dict[str, Any]:
    """用 worker 的 ``build_envelope`` 构造命令信封。

    workspaceId 取全局 ``--workspace-id``（默认 ``ws-local``）；
    ``workspace rename / archive`` 的位置参数与其同名（dest 冲突时位置参数
    胜出），故这两个命令的信封 workspaceId 即目标工作区 id，语义一致。
    """
    command_type = getattr(args, "command_type", None)
    if not command_type:
        raise ValueError("subcommand did not set command_type")
    payload = build_payload(args)
    # 子命令级 --project（如 import）优先于全局 --project-id
    project_id = (
        getattr(args, "project", None) or getattr(args, "project_id", None) or None
    )
    workspace_id = getattr(args, "workspace_id", None) or DEFAULT_WORKSPACE_ID
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
