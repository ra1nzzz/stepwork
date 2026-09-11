"""STEPWORK 命令行入口（W7 Phase 3；PRD-AGT-001 稳定 CLI）。

PRD-AGT-001 的验收是「JSON 输出、退出码和 Job ID」：所有子命令统一把
CommandResult 原样打印为 JSON（含 ``job_id``），失败以非 0 退出码返回。

``python -m cli`` —— 通过 Command Bus 与 worker 后端交互。

所有子命令统一走：构造信封（``source="cli"``、``actor.type="desktop"``）
→ ``asyncio.run(run_command(env))`` → 美化打印结果 JSON 到 stdout。

密钥安全：``config set`` 只能经 ``--file`` / ``--stdin`` 传入完整配置对象，
CLI 永不接收明文密钥参数，也绝不回显密钥明文。
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
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
        "--idempotency-key",
        dest="idempotency_key",
        default=None,
        help=(
            "可选：幂等键（PRD §13）。同一 key 的命令成功后重复提交将直接"
            "返回上次结果，不重复产出、不重复计费"
        ),
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

    # ----- call（通用转发入口 / P4 可达性兜底） -----
    # 「GUI 能做但 CLI 做不到」的兜底：接受**任意** command_type 直接走
    # Command Bus。它给全部路由提供**可达性**，但不提供手感 —— 常用能力仍
    # 应有专门子命令（门禁 C2 在算这笔账，见 scripts/check_ui_parity.py）。
    #
    # ⚠️ dest 必须显式写成 command_type：顶层 `args.command` 存的是**子命令名**
    # （build_payload 靠它路由），位置参数若沿用默认 dest 会把它覆盖掉
    # —— 加 mcp 子命令时就是这么踩了一次。
    call_p = sub.add_parser(
        "call",
        help="通用转发：以任意 command_type 调用（可达性兜底）",
        description=(
            "以任意 command_type 调用 Command Bus，供 CLI 尚无专门子命令的能力"
            "使用。payload 用 --payload-json 传 JSON 对象；用 --list 查看全部命令。"
        ),
    )
    call_p.add_argument(
        "command_type",
        nargs="?",
        metavar="COMMAND_TYPE",
        help="命令名（如 ListPlugins / PreviewPluginManifest）；--list 可查全部",
    )
    call_p.add_argument(
        "--payload-json",
        dest="payload_json",
        help="可选：payload 的 JSON 对象字面量，如 '{\"limit\": 5}'",
    )
    call_p.add_argument(
        "--list",
        dest="list_commands",
        action="store_true",
        help="列出全部可调用的 command_type（JSON 数组）后退出",
    )

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
    # PRD-ANA-003：精确分析（结合场景切分/关键帧），需媒体源 + 可用 ffmpeg
    an.add_argument(
        "--mode",
        choices=("quick", "precise"),
        default="quick",
        help="分析模式：quick 仅转写文本（默认）；precise 结合场景切分/关键帧",
    )
    an.add_argument(
        "--asset-id",
        dest="asset_id",
        help="precise 模式的媒体源素材 id → payload.asset_id",
    )
    an.add_argument(
        "--media-uri",
        dest="media_uri",
        help="precise 模式的媒体源直连 uri → payload.media_uri",
    )
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
    tg.add_argument(
        "--count", type=int, default=5,
        help="生成角度数量（PRD-SCR-001：3—5，默认 5）",
    )
    tg.add_argument(
        "--no-brand",
        dest="no_brand",
        action="store_true",
        help="PRD-BRD-002：本次生成不启用项目品牌档",
    )
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
    sg.add_argument(
        "--no-brand",
        dest="no_brand",
        action="store_true",
        help="PRD-BRD-002：本次生成不启用项目品牌档",
    )
    sg.add_argument("--provider", help="可选：provider 提示，JSON 字符串")

    # PRD-SCR-003：段落级生成/重写/扩写/压缩
    sp = script_sub.add_parser(
        "paragraph", help="段落级编辑（EditParagraph；生成新版本，可回滚）"
    )
    sp.set_defaults(command_type="EditParagraph")
    sp.add_argument("--version-id", dest="version_id", required=True, help="源脚本版本 id")
    sp.add_argument(
        "--index", type=int, required=True, help="目标段落序号（0 起）"
    )
    sp.add_argument(
        "--operation",
        choices=("rewrite", "expand", "condense", "generate"),
        required=True,
        help="段落操作：重写 / 扩写 / 压缩 / 生成",
    )
    sp.add_argument("--instruction", default=None, help="可选：补充要求")
    sp.add_argument(
        "--no-brand",
        dest="no_brand",
        action="store_true",
        help="PRD-BRD-002：本次编辑不启用项目品牌档",
    )

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
    # PRD-REN-005：画幅比例（9:16 优先，另支持 16:9 / 1:1）
    rd.add_argument(
        "--aspect",
        choices=("9:16", "16:9", "1:1"),
        default=None,
        help="画幅比例 → payload.aspect（缺省沿用 worker 默认 9:16）",
    )
    # PRD-REN-003：用户录音替代 TTS（此前 CLI 用户用不到此能力）
    rd.add_argument(
        "--tts-engine",
        dest="tts_engine",
        choices=("synthesize", "user_audio"),
        default=None,
        help="旁白来源：synthesize 合成（默认）/ user_audio 用户录音",
    )
    rd.add_argument(
        "--user-audio",
        dest="user_audio",
        default=None,
        help="user_audio 引擎的录音文件 uri → payload.user_audio_uri",
    )

    # ----- assets（PRD-SRC-003：素材可追溯） -----
    asset = sub.add_parser("assets", help="素材命令（可追溯：来源/作者/权利声明）")
    asset_sub = asset.add_subparsers(dest="assets_action", required=True)

    al = asset_sub.add_parser("list", help="列出项目素材（ListSourceAssets）")
    al.set_defaults(command_type="ListSourceAssets")
    al.add_argument("--limit", type=int, default=None, help="可选：最多返回条数")

    ag = asset_sub.add_parser("get", help="按 id 取素材（GetSourceAsset）")
    ag.set_defaults(command_type="GetSourceAsset")
    ag.add_argument("asset_id", help="素材 id")

    # ----- cleanup（PRD-SRC-005：手动触发清理） -----
    cl = sub.add_parser("cleanup", help="清理临时/下载中间文件（RunCleanup）")
    cl.set_defaults(command_type="RunCleanup")
    cl.add_argument(
        "--mode",
        choices=("immediate", "scheduled"),
        default=None,
        help="immediate 全清 / scheduled 按保留期清（缺省用工作区配置）",
    )

    # ----- audit（PRD-ANA-006：执行后可审计） -----
    au = sub.add_parser("audit", help="查询审计事件（ListAuditEvents）")
    au.set_defaults(command_type="ListAuditEvents")
    au.add_argument("--event-type", dest="event_type", default=None, help="按类型过滤")
    au.add_argument("--limit", type=int, default=None, help="可选：最多返回条数")

    # ----- templates（PRD-REN-005：可发现的模板/画幅清单） -----
    tpl = sub.add_parser("templates", help="列出渲染模板与画幅（ListRenderTemplates）")
    tpl.set_defaults(command_type="ListRenderTemplates")

    # ----- job -----
    job = sub.add_parser("job", help="任务查询命令")
    job_sub = job.add_subparsers(dest="job_action", required=True)

    js = job_sub.add_parser("status", help="查询任务状态（GetJobStatus）")
    js.set_defaults(command_type="GetJobStatus")
    js.add_argument("job_id", help="任务 id")

    # PRD §13.2「CLI 与 UI 同命令一致结果」：此前 CLI 无取消入口
    jc = job_sub.add_parser("cancel", help="取消任务（CancelJob）")
    jc.set_defaults(command_type="CancelJob")
    jc.add_argument("job_id", help="任务 id")

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

    # ----- mcp（S6：出站 MCP 连接，与 GUI 的 Agent Connections 页对等） -----
    # 没有这组子命令时，「登记热点 MCP」只能靠脚本直接调 AddMcpServer ——
    # 而这正是 S5 真机验收里被迫做的事（见 .workbuddy/hotspot-acceptance/）。
    mcp = sub.add_parser("mcp", help="出站 MCP Server：登记 / 看工具 / 调用")
    mcp_sub = mcp.add_subparsers(dest="mcp_action", required=True)

    mcp_add = mcp_sub.add_parser("add", help="登记并探测一个 MCP Server（AddMcpServer）")
    mcp_add.set_defaults(command_type="AddMcpServer")
    mcp_add.add_argument(
        "--command",
        # ⚠️ dest 不能叫 "command"：顶层 `args.command` 存的是**子命令名**，
        # build_payload 就靠它路由。同名会把子命令名覆盖成启动命令
        # （实测报 `unknown command: 'python -m my_server'`）。
        dest="server_command",
        required=True,
        help='启动命令，含参数（如 "python -m my_server"）；登记时会先探测，连不上不落库',
    )
    mcp_add.add_argument("--name", help="可选：连接名（默认取命令首段）")

    mcp_ls = mcp_sub.add_parser("tools", help="列出某连接的工具（ListMcpTools）")
    mcp_ls.set_defaults(command_type="ListMcpTools")
    mcp_ls.add_argument(
        "--connection-id", dest="connection_id", required=True, help="连接 id（mcp add 返回）"
    )

    mcp_call = mcp_sub.add_parser("call", help="调用远端工具（CallMcpTool）")
    mcp_call.set_defaults(command_type="CallMcpTool")
    mcp_call.add_argument(
        "--connection-id", dest="connection_id", required=True, help="连接 id"
    )
    mcp_call.add_argument("--tool", dest="tool_name", required=True, help="工具名")
    mcp_call.add_argument(
        "--args-json",
        dest="args_json",
        help="可选：工具参数（JSON 对象字面量，如 '{\"limit\": 5}'）",
    )

    # ----- hotspots（S5：上游热点发现 / 推荐 / 反馈） -----
    hs = sub.add_parser("hotspots", help="上游热点：发现 / 推荐 / 反馈")
    hs_sub = hs.add_subparsers(dest="hotspots_action", required=True)

    hss = hs_sub.add_parser("sources", help="列出可用热点源（ListHotspotSources）")
    hss.set_defaults(command_type="ListHotspotSources")
    hss.add_argument("--connection-id", dest="connection_id", help="MCP 连接 id（缺省自动找）")

    hsd = hs_sub.add_parser("discover", help="抓取热点并落库（DiscoverHotspots）")
    hsd.set_defaults(command_type="DiscoverHotspots")
    hsd.add_argument(
        "--source",
        dest="sources",
        action="append",
        metavar="SOURCE",
        help="指定源（可重复）；不给 = 全部免登录源（热点宝需显式点名）",
    )
    hsd.add_argument("--limit", type=int, default=30, help="最多条数（1-100，默认 30）")
    hsd.add_argument(
        "--window-hours", dest="window_hours", type=int, default=48, help="只看最近 N 小时"
    )
    hsd.add_argument("--query", help="标题/摘要包含该词")
    hsd.add_argument("--connection-id", dest="connection_id", help="MCP 连接 id（缺省自动找）")
    hsd.add_argument(
        "--no-save", dest="save", action="store_false", help="只返回不落库（跳过推荐去重）"
    )

    hsr = hs_sub.add_parser("recommend", help="按品牌与历史推荐热点（RecommendHotspots）")
    hsr.set_defaults(command_type="RecommendHotspots")
    hsr.add_argument("--batch-id", dest="batch_id", help="推荐指定批次（默认最近一批）")
    hsr.add_argument(
        "--source",
        dest="sources",
        action="append",
        metavar="SOURCE",
        help="跨批次按源筛（可重复）",
    )
    hsr.add_argument("--limit", type=int, default=10, help="返回条数（1-50，默认 10）")
    hsr.add_argument(
        "--reason-top-n",
        dest="reason_top_n",
        type=int,
        default=5,
        help="让 AI 写理由的条数（0 = 全用规则模板）",
    )
    hsr.add_argument(
        "--no-brand",
        dest="use_brand_profile",
        action="store_false",
        help="不使用项目品牌画像打分",
    )
    hsr.add_argument("--provider", help="AI Provider hint（写理由用）")

    hsf = hs_sub.add_parser("feedback", help="记录对某条热点的态度（RecordHotspotFeedback）")
    hsf.set_defaults(command_type="RecordHotspotFeedback")
    hsf.add_argument("--hotspot-id", dest="hotspot_id", required=True, help="热点 id")
    hsf.add_argument(
        "--verdict",
        required=True,
        choices=("adopted", "ignored", "rejected"),
        help="adopted 采纳 / ignored 不感兴趣 / rejected 明确不合适",
    )
    hsf.add_argument("--reason", help="可选：理由")
    hsf.add_argument("--project", help="可选：关联项目 id")

    hsc = hs_sub.add_parser(
        "convert", help="把热点转成选题简报（ConvertHotspotToTopic）"
    )
    hsc.set_defaults(command_type="ConvertHotspotToTopic")
    hsc.add_argument("--hotspot-id", dest="hotspot_id", required=True, help="热点 id")
    hsc.add_argument(
        "--reason",
        help="可选：推荐页展示的那句理由，原样带入简报（不传则不编）",
    )
    hsc.add_argument(
        "--reason-source",
        dest="reason_source",
        choices=("ai", "rule", "human"),
        help="可选：理由的来源，缺省不记（brief 与 producer 会写成 none）",
    )
    hsc.add_argument(
        "--breakdown-json",
        dest="breakdown_json",
        help="可选：RecommendHotspots 出参里的 breakdown（原始 JSON），原样带入",
    )
    hsc.add_argument("--project", help="可选：落到哪个项目（默认默认项目）")

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

    # ----- scenes（S2：分幕 video_scenes）-----
    # P4：命令总线加了入口就必须同时给 CLI，否则「GUI 能用 CLI 用不了」。
    sc = sub.add_parser("scenes", help="分幕命令（S2 video_scenes）")
    sc_sub = sc.add_subparsers(dest="scenes_action", required=True)

    scs = sc_sub.add_parser("save", help="保存分幕（SaveVideoScenes）")
    scs.set_defaults(command_type="SaveVideoScenes")
    scs.add_argument("--version-id", dest="version_id", required=True, help="脚本版本 id")
    scs.add_argument(
        "--file",
        metavar="PATH",
        help='分幕 JSON 文件：[{"seq":0,"text":"...","highlight":"..."}]',
    )
    scs.add_argument(
        "--stdin", action="store_true", help="从标准输入读取分幕 JSON"
    )
    scs.add_argument(
        "--append",
        action="store_true",
        help="追加而非替换（默认替换该版本的全部分幕）",
    )

    scl = sc_sub.add_parser("list", help="列出分幕（ListVideoScenes）")
    scl.set_defaults(command_type="ListVideoScenes")
    scl.add_argument("--version-id", dest="version_id", required=True, help="脚本版本 id")

    scu = sc_sub.add_parser("update", help="回填单幕产出（UpdateVideoScene）")
    scu.set_defaults(command_type="UpdateVideoScene")
    scu.add_argument("--scene-id", dest="scene_id", required=True, help="分幕 id")
    scu.add_argument("--audio-uri", dest="audio_uri", help="该幕配音 uri")
    scu.add_argument("--image-uri", dest="image_uri", help="该幕配图 uri")
    scu.add_argument(
        "--duration-sec", dest="duration_sec", type=float, help="TTS 实测时长（秒）"
    )
    scu.add_argument(
        "--born-at-sec", dest="born_at_sec", type=float, help="该幕首句起始秒（抽帧目检用）"
    )

    scy = sc_sub.add_parser("synth", help="逐幕配音并回填时间轴（SynthesizeScenes）")
    scy.set_defaults(command_type="SynthesizeScenes")
    scy.add_argument("--version-id", dest="version_id", required=True, help="脚本版本 id")
    scy.add_argument("--out-dir", dest="out_dir", help="音频输出目录")
    scy.add_argument(
        "--no-concat",
        dest="concat",
        action="store_false",
        help="不拼整轨（只回填每幕的 audio_uri / duration_sec / start_sec）",
    )

    sci = sc_sub.add_parser("illustrate", help="逐幕配图并回填（IllustrateScenes）")
    sci.set_defaults(command_type="IllustrateScenes")
    sci.add_argument("--version-id", dest="version_id", required=True, help="脚本版本 id")
    sci.add_argument("--style", help="美术风格（默认 xiaohei）")
    sci.add_argument("--out-dir", dest="out_dir", help="图片输出目录")
    sci.add_argument("--prompt-extra", dest="prompt_extra", help="追加到提示词的补充要求")
    sci.add_argument(
        "--size",
        help="出图尺寸 WxH（如 1088x1920；不给则用厂商预置/STEPWORK_IMAGE_SIZE）",
    )
    sci.add_argument(
        "--force",
        action="store_true",
        help="已有图的幕也重新生成（默认跳过，生图要钱）",
    )

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


def _json_object(raw: str, flag: str) -> dict[str, Any]:
    """把 ``--xxx-json`` 的字符串解析成 JSON 对象。

    在 CLI 就拦下解析失败：别让它变成一次「命令已下发但信封被拒」的往返，
    用户看不出是自己参数写坏了。**必须是对象**（数组/标量没有对应的 payload 形状）。
    """
    try:
        parsed = json.loads(raw)
    except ValueError as e:
        raise ValueError(f"{flag} 不是合法 JSON: {e}") from None
    if not isinstance(parsed, dict):
        raise ValueError(f"{flag} 必须是 JSON 对象")
    return parsed


def route_table() -> dict[str, str]:
    """权威路由表（``bus._ROUTES``）的只读引用。

    仅在 ``call --list`` 与目标校验时用 —— 刻意问后端要事实，而不是在 CLI
    里维护第二份命令清单（那种副本一定会与真实路由脱节）。
    """
    from worker.runtime.commands.bus import _ROUTES  # noqa: PLC0415

    return _ROUTES


#: 通用转发**拒绝**的命令：它们已有专门的、带安全设计的 CLI 入口。
#: 走 ``call --payload-json`` 会把密钥放进 argv / shell history，破坏
#: 「CLI 永不接收明文密钥参数」这条不变量（cli/config.py 的 SET.7 约束）。
#: 逃生舱补的是**缺口**，不该顺手把已有约束绕掉。
_CALL_DENY: dict[str, str] = {
    "UpdateConfig": "请用 `config set --file <path>` 或 `--stdin`（密钥不进 argv）",
}


def resolve_call_target(raw: str) -> str:
    """校验通用转发（``call``）的目标命令名。

    Raises:
        ValueError: 命令被拒转发，或不存在（附近似建议）。
    """
    denied = _CALL_DENY.get(raw)
    if denied:
        raise ValueError(f"{raw} 不走通用转发：{denied}")
    routes = route_table()
    if raw not in routes:
        near = difflib.get_close_matches(raw, sorted(routes), n=3, cutoff=0.6)
        hint = f"；你是不是想用 {' / '.join(near)}" if near else ""
        raise ValueError(f"unknown command_type: {raw}{hint}（用 `call --list` 查看全部）")
    return raw


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    """根据子命令把解析后的参数映射为命令 payload。"""
    command = getattr(args, "command", None)

    if command == "call":
        # payload 由调用方原样提供（目标命令自己才是它的权威解释者）；
        # 不给就是空对象 —— 别在这里替目标命令猜默认值。
        raw_payload = getattr(args, "payload_json", None)
        if raw_payload is None:
            return {}
        return _json_object(raw_payload, "--payload-json")

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
        # 仅在 precise 时下发 mode 与媒体源，保持 quick 的信封与旧版一致
        if getattr(args, "mode", "quick") == "precise":
            payload["mode"] = "precise"
            if args.asset_id:
                payload["asset_id"] = args.asset_id
            if args.media_uri:
                payload["media_uri"] = args.media_uri
        provider = _parse_provider(getattr(args, "provider", None))
        if provider is not None:
            payload["provider"] = provider
        return payload

    if command == "topic":
        payload = {
            "source_version_id": args.source_version_id,
            "count": args.count,
        }
        # 仅在显式关闭时下发，保持默认信封与旧版一致
        if getattr(args, "no_brand", False):
            payload["use_brand_profile"] = False
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
            if getattr(args, "no_brand", False):
                payload["use_brand_profile"] = False
            provider = _parse_provider(getattr(args, "provider", None))
            if provider is not None:
                payload["provider"] = provider
            return payload
        if action == "paragraph":
            para_payload: dict[str, Any] = {
                "version_id": args.version_id,
                "paragraph_index": args.index,
                "operation": args.operation,
            }
            if getattr(args, "instruction", None):
                para_payload["instruction"] = args.instruction
            if getattr(args, "no_brand", False):
                para_payload["use_brand_profile"] = False
            return para_payload
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

    if command == "scenes":
        action = getattr(args, "scenes_action", None)
        if action == "save":
            raw = None
            if getattr(args, "stdin", False):
                raw = sys.stdin.read()
            elif getattr(args, "file", None):
                with open(args.file, encoding="utf-8") as f:
                    raw = f.read()
            if not raw:
                raise ValueError("scenes save requires --file or --stdin")
            try:
                scenes = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(f"scenes JSON 解析失败: {e}") from None
            if not isinstance(scenes, list):
                raise ValueError("scenes JSON 必须是数组")
            return {
                "versionId": args.version_id,
                "scenes": scenes,
                # 默认替换；--append 显式改为追加
                "replace": not getattr(args, "append", False),
            }
        if action == "list":
            return {"versionId": args.version_id}
        if action == "update":
            scene_payload: dict[str, Any] = {"sceneId": args.scene_id}
            # 契约：只回填配音/配图产出，None 一律不下发（handler 会拒空）
            for camel, value in (
                ("audioUri", getattr(args, "audio_uri", None)),
                ("imageUri", getattr(args, "image_uri", None)),
                ("durationSec", getattr(args, "duration_sec", None)),
                ("bornAtSec", getattr(args, "born_at_sec", None)),
            ):
                if value is not None:
                    scene_payload[camel] = value
            return scene_payload
        if action == "synth":
            synth_payload: dict[str, Any] = {
                "versionId": args.version_id,
                "concat": bool(getattr(args, "concat", True)),
            }
            if getattr(args, "out_dir", None):
                synth_payload["outDir"] = args.out_dir
            return synth_payload
        if action == "illustrate":
            illus_payload: dict[str, Any] = {
                "versionId": args.version_id,
                "force": bool(getattr(args, "force", False)),
            }
            # 契约：未给的一律不下发（handler 用自己的默认值）
            for camel, value in (
                ("style", getattr(args, "style", None)),
                ("outDir", getattr(args, "out_dir", None)),
                ("promptExtra", getattr(args, "prompt_extra", None)),
            ):
                if value is not None:
                    illus_payload[camel] = value
            raw_size = getattr(args, "size", None)
            if raw_size:
                try:
                    w_str, h_str = str(raw_size).lower().split("x")
                    illus_payload["size"] = {"width": int(w_str), "height": int(h_str)}
                except ValueError:
                    raise ValueError(
                        f"--size 需形如 1088x1920，收到 {raw_size!r}"
                    ) from None
            return illus_payload
        raise ValueError(f"unknown scenes action: {action!r}")

    if command == "import":
        return _import_payload(args.file)

    if command == "transcribe":
        # 对齐桌面端 useTranscriptStore：opts 缺省为空对象
        return {"asset_id": args.asset_id, "opts": {}}

    if command == "render":
        # 其余 RenderSpec 字段（resolution / fps）由 worker 缺省补齐
        # 独立命名，避免与上文 analyze 分支的 payload 复用同名（mypy no-redef）
        render_payload: dict[str, Any] = {
            "source_version_id": args.version_id,
            "template": args.template,
        }
        if getattr(args, "aspect", None):
            render_payload["aspect"] = args.aspect
        if getattr(args, "tts_engine", None):
            render_payload["tts_engine"] = args.tts_engine
        if getattr(args, "user_audio", None):
            render_payload["user_audio_uri"] = args.user_audio
        return render_payload

    if command == "templates":
        return {}

    if command == "assets":
        action = getattr(args, "assets_action", None)
        if action == "list":
            return {"limit": args.limit} if args.limit is not None else {}
        if action == "get":
            return {"assetId": args.asset_id}
        raise ValueError(f"unknown assets action: {action!r}")

    if command == "cleanup":
        return {"mode": args.mode} if args.mode else {}

    if command == "audit":
        audit_payload: dict[str, Any] = {}
        if getattr(args, "event_type", None):
            audit_payload["eventType"] = args.event_type
        if args.limit is not None:
            audit_payload["limit"] = args.limit
        return audit_payload

    if command == "job":
        action = getattr(args, "job_action", None)
        if action == "status":
            return {"job_id": args.job_id}
        if action == "cancel":
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

    if command == "mcp":
        action = getattr(args, "mcp_action", None)
        if action == "add":
            add_payload: dict[str, Any] = {"command": args.server_command}
            if getattr(args, "name", None):
                add_payload["name"] = args.name
            return add_payload
        if action == "tools":
            return {"connectionId": args.connection_id}
        if action == "call":
            call_payload: dict[str, Any] = {
                "connectionId": args.connection_id,
                "toolName": args.tool_name,
            }
            raw_args = getattr(args, "args_json", None)
            if raw_args is not None:
                call_payload["arguments"] = _json_object(raw_args, "--args-json")
            return call_payload
        raise ValueError(f"unknown mcp action: {action!r}")

    if command == "hotspots":
        action = getattr(args, "hotspots_action", None)
        if action == "sources":
            hs_payload: dict[str, Any] = {}
            if getattr(args, "connection_id", None):
                hs_payload["connectionId"] = args.connection_id
            return hs_payload
        if action == "discover":
            discover_payload: dict[str, Any] = {
                "limit": args.limit,
                "windowHours": args.window_hours,
                "save": bool(getattr(args, "save", True)),
            }
            if getattr(args, "sources", None):
                discover_payload["sources"] = args.sources
            # 其余可选项未给就不下发：handler 用自己的默认值，不让 CLI 替它决定
            for camel, value in (
                ("query", getattr(args, "query", None)),
                ("connectionId", getattr(args, "connection_id", None)),
            ):
                if value is not None:
                    discover_payload[camel] = value
            return discover_payload
        if action == "recommend":
            recommend_payload: dict[str, Any] = {
                "limit": args.limit,
                "reasonTopN": args.reason_top_n,
                "useBrandProfile": bool(getattr(args, "use_brand_profile", True)),
            }
            for camel, value in (
                ("batchId", getattr(args, "batch_id", None)),
                ("provider", getattr(args, "provider", None)),
            ):
                if value is not None:
                    recommend_payload[camel] = value
            if getattr(args, "sources", None):
                recommend_payload["sources"] = args.sources
            return recommend_payload
        if action == "feedback":
            feedback_payload: dict[str, Any] = {
                "hotspotId": args.hotspot_id,
                "verdict": args.verdict,
            }
            for camel, value in (
                ("reason", getattr(args, "reason", None)),
                ("projectId", getattr(args, "project", None)),
            ):
                if value is not None:
                    feedback_payload[camel] = value
            return feedback_payload
        if action == "convert":
            convert_payload: dict[str, Any] = {"hotspotId": args.hotspot_id}
            for camel, value in (
                ("reason", getattr(args, "reason", None)),
                ("reasonSource", getattr(args, "reason_source", None)),
                ("projectId", getattr(args, "project", None)),
            ):
                if value is not None:
                    convert_payload[camel] = value
            raw_breakdown = getattr(args, "breakdown_json", None)
            if raw_breakdown is not None:
                convert_payload["breakdown"] = _json_object(
                    raw_breakdown, "--breakdown-json"
                )
            return convert_payload
        raise ValueError(f"unknown hotspots action: {action!r}")

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
    # 通用转发的命令名来自用户输入，先校验再下发：未知命令在 CLI 就拦下并给
    # 近似建议，比「信封下发后拿到 unknown commandType」有用得多。
    if getattr(args, "command", None) == "call" and command_type:
        command_type = resolve_call_target(command_type)
    if not command_type:
        if getattr(args, "command", None) == "call":
            raise ValueError("call 需要 COMMAND_TYPE（或用 `call --list` 查看全部）")
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
        idempotency_key=getattr(args, "idempotency_key", None),
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

    # `call --list`：把权威路由表原样打出来（可发现性 —— 用不了看不见的命令）。
    # 不构造信封，因此也不会碰到下面的 CLI_ARGUMENT 分支。
    if getattr(args, "list_commands", False):
        print(json.dumps(sorted(route_table()), indent=2, ensure_ascii=False))
        return 0

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
