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
from worker.runtime.publish.platforms import PLATFORM_RULES

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

    ad = asset_sub.add_parser("delete", help="删除素材记录（DeleteAsset）")
    ad.set_defaults(command_type="DeleteAsset")
    ad.add_argument(
        "--id", dest="asset_id", required=True, help="素材 id → payload.assetId"
    )
    ad.add_argument(
        "--project",
        dest="project_id",
        default=None,
        help="可选：素材所属项目 id → 信封 projectId（handler 按 assetId 定位，一般不必给）",
    )

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

    px = proj_sub.add_parser("export", help="导出项目包为 zip（ExportProject）")
    px.set_defaults(command_type="ExportProject")
    px.add_argument("--id", dest="project_id", required=True, help="项目 id → payload.projectId")
    px.add_argument(
        "--no-assets",
        dest="include_assets",
        action="store_false",
        help="不打包素材文件（默认打包）",
    )
    px.add_argument(
        "--no-jobs",
        dest="include_jobs",
        action="store_false",
        help="不打包任务历史（默认打包）",
    )

    pi = proj_sub.add_parser("import", help="导入项目包（ImportProject）")
    pi.set_defaults(command_type="ImportProject")
    pi.add_argument(
        "--bundle-path",
        dest="bundle_path",
        required=True,
        help="项目 zip 的绝对路径 → payload.bundlePath",
    )
    pi.add_argument(
        "--keep-ids",
        dest="remap_id",
        action="store_false",
        help="保留包内原 id（默认重新分配，避免与现有项目撞 id）",
    )

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

    bu = brand_sub.add_parser("update", help="更新品牌档（UpdateBrandProfile）")
    bu.set_defaults(command_type="UpdateBrandProfile")
    bu.add_argument("--id", dest="profile_id", required=True, help="品牌档 id → payload.profileId")
    # 与 create 同名同义：只有显式给出的字段才进 payload（未给的列不动）
    bu.add_argument("--name", help="可选：名称")
    bu.add_argument("--tone", help="可选：语气")
    bu.add_argument("--positioning", help="可选：定位")
    bu.add_argument("--audience", help="可选：受众")
    bu.add_argument(
        "--pillar",
        dest="pillars",
        action="append",
        metavar="PILLAR",
        help="可选：内容支柱（可重复，整体替换）→ payload.contentPillars",
    )
    bu.add_argument(
        "--banned",
        dest="banned",
        action="append",
        metavar="EXPR",
        help="可选：禁用表达（可重复，整体替换）→ payload.bannedExpressions",
    )

    bs = brand_sub.add_parser("scripts", help="列出品牌历史脚本（ListBrandScripts）")
    bs.set_defaults(command_type="ListBrandScripts")
    bs.add_argument(
        "--profile-id", dest="profile_id", required=True, help="品牌档 id → payload.profileId"
    )
    bs.add_argument("--keyword", default=None, help="可选：标题 / 正文关键词过滤")

    bsi = brand_sub.add_parser("script-import", help="导入历史脚本（ImportBrandScript）")
    bsi.set_defaults(command_type="ImportBrandScript")
    bsi.add_argument(
        "--profile-id", dest="profile_id", required=True, help="品牌档 id → payload.profileId"
    )
    # 正文可能很长：--content 走短句内联，--file 走文件（与 `analysis save` 同款）
    bsi.add_argument(
        "--content", default=None, help="脚本正文（与 --file 二选一）→ payload.content"
    )
    bsi.add_argument(
        "--file",
        dest="content_file",
        metavar="PATH",
        default=None,
        help="脚本正文文件（与 --content 二选一）",
    )
    bsi.add_argument("--title", default=None, help="可选：标题 → payload.title")
    bsi.add_argument("--source", default=None, help="可选：来源标记（如 manual / douyin）")

    bsd = brand_sub.add_parser("script-delete", help="删除历史脚本（DeleteBrandScript）")
    bsd.set_defaults(command_type="DeleteBrandScript")
    bsd.add_argument(
        "--script-id", dest="script_id", required=True, help="脚本 id → payload.scriptId"
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

    vd = ver_sub.add_parser("diff", help="比较两个内容版本（DiffContentVersions）")
    vd.set_defaults(command_type="DiffContentVersions")
    vd.add_argument(
        "--version-id", dest="version_id", required=True, help="目标版本 id → payload.versionId"
    )
    vd.add_argument(
        "--base-version-id",
        dest="base_version_id",
        default=None,
        help="可选：基线版本 id → payload.baseVersionId（缺省由 handler 取上一版）",
    )

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
        # **从规则表派生**，不手写副本 —— 此前这里硬编码 ("douyin", "generic")，
        # 而后端支持 5 个平台：`--platform bilibili` 被 CLI 拒掉，同一件事从别的
        # 入口发却是通的。这是同一类漂移的**第三份副本**（publish_common.py 的
        # 平台白名单修过一次），故这次直接消除副本本身而非补全列表。
        choices=sorted(PLATFORM_RULES),
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

    ptl = pub_sub.add_parser(
        "timeline", help="导出可继续剪辑的时间线（ExportEditTimeline）"
    )
    ptl.set_defaults(command_type="ExportEditTimeline")
    ptl.add_argument(
        "--project", dest="project_id", required=True, help="项目 id → payload.projectId"
    )
    ptl.add_argument(
        "--format",
        default="otio",
        choices=["otio", "edl"],
        help="导出格式：otio（Resolve/Premiere/FCP/Avid 都读）或 edl（最大公约数）",
    )

    pfl = pub_sub.add_parser(
        "fill", help="生成平台填充包（BuildPlatformFillPackage；不点最终发布）"
    )
    pfl.set_defaults(command_type="BuildPlatformFillPackage")
    pfl.add_argument("--variant-id", dest="variant_id", required=True, help="平台变体 id")
    pfl.add_argument(
        "--cover", dest="cover_path", default=None, help="可选：封面图路径 → payload.coverPath"
    )
    pfl.add_argument(
        "--scheduled-at",
        dest="scheduled_at",
        default=None,
        help="可选：带时区的 ISO 时间 → payload.scheduledAt（走平台原生定时字段）",
    )

    ppp = pub_sub.add_parser(
        "provider",
        help="探测发布 Provider 是否可用（ProbePublishProvider；只探状态不发布）",
    )
    ppp.set_defaults(command_type="ProbePublishProvider")

    ptr = pub_sub.add_parser(
        "auth-request", help="申请一次性发布授权（RequestPublishAuthorization）"
    )
    ptr.set_defaults(command_type="RequestPublishAuthorization")
    ptr.add_argument("--variant-id", dest="variant_id", required=True, help="平台变体 id")

    psc = pub_sub.add_parser("schedule", help="排一条定时发布（SchedulePublish）")
    psc.set_defaults(command_type="SchedulePublish")
    psc.add_argument("--variant-id", dest="variant_id", required=True, help="平台变体 id")
    psc.add_argument(
        "--at",
        dest="scheduled_at",
        required=True,
        help="带时区的 ISO 时间 → payload.scheduledAt（不带时区会按 UTC 解释，容易差几小时）",
    )
    psc.add_argument("--note", default=None, help="可选：备注 → payload.note")

    psu = pub_sub.add_parser("unschedule", help="取消一条排期（CancelScheduledPublish）")
    psu.set_defaults(command_type="CancelScheduledPublish")
    psu.add_argument(
        "--schedule-id", dest="schedule_id", required=True, help="排期 id → payload.scheduleId"
    )

    psl = pub_sub.add_parser("schedules", help="列出排期（ListScheduledPublishes）")
    psl.set_defaults(command_type="ListScheduledPublishes")
    psl.add_argument(
        "--project",
        dest="project_id",
        default=None,
        help="可选：项目 id → payload.projectId（缺省返回全部）",
    )
    psl.add_argument("--status", default=None, help="可选：按状态过滤")

    pfd = pub_sub.add_parser(
        "fire-due", help="触发全部到点排期（FireDueSchedules）"
    )
    pfd.set_defaults(command_type="FireDueSchedules")

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

    # ===== S6 第二批：GUI/CLI 一等公民对等 =====
    # 下面这些组是为了把 scripts/check_ui_parity.py 的 C2 缺口（GUI 有专门
    # 界面、CLI 只能走通用 `call`）一条条补掉。通用 `call` 保的是**可达性**，
    # 这里补的是**手感**：常用路径要有名字、有 --help、有必填校验。
    #
    # 键名一律照 handler 实际读的写（多数两种都收，取 camelCase 与 GUI 一致）；
    # 改这些 payload 前先回读对应 handler 的 `p.get(...)`，别凭印象。

    # ----- plugin（插件：装 / 卸 / 启停 / 健康检查） -----
    plg = sub.add_parser("plugin", help="插件：列出 / 装 / 卸 / 启停 / 健康检查")
    plg_sub = plg.add_subparsers(dest="plugin_action", required=True)

    plgl = plg_sub.add_parser("list", help="列出已安装插件（ListPlugins）")
    plgl.set_defaults(command_type="ListPlugins")

    plgp = plg_sub.add_parser(
        "preview", help="读取插件目录的 manifest 与权限（PreviewPluginManifest）"
    )
    plgp.set_defaults(command_type="PreviewPluginManifest")
    plgp.add_argument(
        "--path", required=True, help="含 manifest.json 的插件目录 → payload.path"
    )

    plgi = plg_sub.add_parser("install", help="安装插件（InstallPlugin）")
    plgi.set_defaults(command_type="InstallPlugin")
    plgi.add_argument(
        "--path", required=True, help="含 manifest.json 的插件目录 → payload.path"
    )

    plgu = plg_sub.add_parser("uninstall", help="卸载插件（UninstallPlugin）")
    plgu.set_defaults(command_type="UninstallPlugin")
    plgu.add_argument("--id", dest="plugin_id", required=True, help="插件 id → payload.pluginId")

    plge = plg_sub.add_parser("enable", help="启用插件（EnablePlugin）")
    plge.set_defaults(command_type="EnablePlugin")
    plge.add_argument("--id", dest="plugin_id", required=True, help="插件 id → payload.pluginId")

    plgd = plg_sub.add_parser("disable", help="停用插件（DisablePlugin）")
    plgd.set_defaults(command_type="DisablePlugin")
    plgd.add_argument("--id", dest="plugin_id", required=True, help="插件 id → payload.pluginId")

    plgh = plg_sub.add_parser("health", help="插件健康检查（CheckPluginHealth）")
    plgh.set_defaults(command_type="CheckPluginHealth")
    plgh.add_argument("--id", dest="plugin_id", required=True, help="插件 id → payload.pluginId")

    # ----- agent（出站 Agent 连接与任务） -----
    agt = sub.add_parser("agent", help="Agent 连接与任务：查看 / 启停 / 解绑")
    agt_sub = agt.add_subparsers(dest="agent_action", required=True)

    agtl = agt_sub.add_parser("connections", help="列出 Agent 连接（ListAgentConnections）")
    agtl.set_defaults(command_type="ListAgentConnections")

    agtt = agt_sub.add_parser("tasks", help="列出 Agent 任务（ListAgentTasks）")
    agtt.set_defaults(command_type="ListAgentTasks")

    agta = agt_sub.add_parser("artifacts", help="列出 Agent 产物（ListAgentArtifacts）")
    agta.set_defaults(command_type="ListAgentArtifacts")

    agts = agt_sub.add_parser(
        "set-status", help="启用 / 停用某条连接（SetAgentConnectionStatus）"
    )
    agts.set_defaults(command_type="SetAgentConnectionStatus")
    agts.add_argument(
        "--id", dest="connection_id", required=True, help="连接 id → payload.connectionId"
    )
    agts.add_argument(
        "--status",
        required=True,
        choices=["active", "inactive"],
        help="目标状态 → payload.status",
    )

    agtd = agt_sub.add_parser("disconnect", help="删除某条连接（DeleteAgentConnection）")
    agtd.set_defaults(command_type="DeleteAgentConnection")
    agtd.add_argument(
        "--id", dest="connection_id", required=True, help="连接 id → payload.connectionId"
    )

    # ----- a2a（入站 Server 开关 + 出站对端登记） -----
    a2a = sub.add_parser("a2a", help="A2A：入站 Server 开关 + 出站对端登记")
    a2a_sub = a2a.add_subparsers(dest="a2a_action", required=True)

    a2a_add = a2a_sub.add_parser(
        "add", help="登记并探测一个 A2A 对端（AddA2aAgent）"
    )
    a2a_add.set_defaults(command_type="AddA2aAgent")
    a2a_add.add_argument("--url", required=True, help="对端 base url → payload.url")
    a2a_add.add_argument(
        "--token",
        default=None,
        help=(
            "可选：对端要求的 Bearer token → payload.token。"
            "这是**单次调用的对端凭据**（不落库、不进日志），与 config 的"
            "「密钥永不进 argv」约束无关"
        ),
    )

    a2a_start = a2a_sub.add_parser(
        "start", help="启动入站 A2A Server（StartA2aServer；token 仅本次返回）"
    )
    a2a_start.set_defaults(command_type="StartA2aServer")
    a2a_start.add_argument(
        "--port", type=int, default=None, help="可选：监听端口 → payload.port（缺省由 worker 决定）"
    )

    a2a_stop = a2a_sub.add_parser("stop", help="停止入站 A2A Server（StopA2aServer）")
    a2a_stop.set_defaults(command_type="StopA2aServer")

    a2a_status = a2a_sub.add_parser(
        "status", help="查看入站 A2A Server 状态（GetA2aServerStatus）"
    )
    a2a_status.set_defaults(command_type="GetA2aServerStatus")

    # ----- acp（本地 ACP Agent 子进程） -----
    acp = sub.add_parser("acp", help="ACP：登记本地 ACP Agent 子进程")
    acp_sub = acp.add_subparsers(dest="acp_action", required=True)

    acp_add = acp_sub.add_parser("add", help="登记并启动本地 ACP Agent（AddAcpAgent）")
    acp_add.set_defaults(command_type="AddAcpAgent")
    acp_add.add_argument(
        "--command",
        # ⚠️ dest 不能叫 "command"：顶层 `args.command` 存的是子命令名，
        # build_payload 靠它路由。mcp add 就是在这里踩过一次。
        dest="acp_command",
        required=True,
        help="启动命令 → payload.command",
    )

    # ----- approvals（审批中心） -----
    apv = sub.add_parser("approvals", help="审批中心：待办列表 / 批准或驳回")
    apv_sub = apv.add_subparsers(dest="approvals_action", required=True)

    apvl = apv_sub.add_parser("list", help="列出审批请求（ListApprovalRequests）")
    apvl.set_defaults(command_type="ListApprovalRequests")
    apvl.add_argument("--status", default=None, help="可选：按状态过滤（如 pending）")
    apvl.add_argument("--limit", type=int, default=None, help="可选：最多返回条数")

    apvd = apv_sub.add_parser(
        "decide", help="批准 / 驳回一条审批（DecideApprovalRequest）"
    )
    apvd.set_defaults(command_type="DecideApprovalRequest")
    apvd.add_argument(
        "--id", dest="approval_id", required=True, help="审批 id → payload.approvalId"
    )
    apvd.add_argument(
        "--decision", required=True, choices=["approve", "reject"], help="决定 → payload.decision"
    )

    # ----- diagnostics（脱敏诊断包） -----
    dgn = sub.add_parser("diagnostics", help="诊断包导出（默认脱敏）")
    dgn_sub = dgn.add_subparsers(dest="diagnostics_action", required=True)

    dgne = dgn_sub.add_parser("export", help="导出诊断包（ExportDiagnosticsBundle）")
    dgne.set_defaults(command_type="ExportDiagnosticsBundle")
    dgne.add_argument(
        "--desensitize",
        dest="desensitize",
        action="store_true",
        default=None,
        help="强制脱敏（不指定时跟随配置）",
    )
    dgne.add_argument(
        "--no-desensitize",
        dest="desensitize",
        action="store_false",
        default=None,
        help="不脱敏 —— **只在本地自查用**，别把这种包发出去",
    )
    dgne.add_argument(
        "--max-log-lines",
        dest="max_log_lines",
        type=int,
        default=None,
        help="可选：最多纳入的日志行数 → payload.maxLogLines",
    )

    # ----- provenance（溯源） -----
    prv = sub.add_parser("provenance", help="溯源：查 Artifact / 版本 / 脚本的来源链")
    prv_sub = prv.add_subparsers(dest="provenance_action", required=True)

    prvg = prv_sub.add_parser("get", help="查询溯源记录（GetProvenance）")
    prvg.set_defaults(command_type="GetProvenance")
    prvg.add_argument(
        "--subject-type",
        dest="subject_type",
        required=True,
        help="主体类型（如 content_version / artifact）→ payload.subjectType",
    )
    prvg.add_argument(
        "--subject-id", dest="subject_id", required=True, help="主体 id → payload.subjectId"
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


def _script_content(args: argparse.Namespace) -> str:
    """取 ``brand script-import`` 的脚本正文：``--content`` 与 ``--file`` 二选一。

    正文动辄上千字，走 argv 既难看又有 shell 引用风险，所以两条路都给：
    短句用 ``--content``，长的写文件走 ``--file``（与 ``analysis save`` 同款）。

    Raises:
        ValueError: 两者都给 / 都不给 / 文件不存在。
    """
    inline: str | None = getattr(args, "content", None)
    path: str | None = getattr(args, "content_file", None)
    if inline and path:
        raise ValueError("--content 与 --file 只能给一个")
    if path:
        if not os.path.isfile(path):
            raise ValueError(f"script file not found: {path}")
        with open(path, encoding="utf-8") as f:
            inline = f.read()
    if not inline or not inline.strip():
        raise ValueError("script-import 需要脚本正文：--content <text> 或 --file <path>")
    return inline


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
        if action == "delete":
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
        if action == "export":
            # 契约（project_io.py）：includeAssets / includeJobs 默认 true，
            # 这里显式发出去 —— 默认值写在两个地方迟早对不上，发明确值最省事
            return {
                "projectId": args.project_id,
                "includeAssets": args.include_assets,
                "includeJobs": args.include_jobs,
            }
        if action == "import":
            return {"bundlePath": args.bundle_path, "remapId": args.remap_id}
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
        if action == "update":
            # 语义是 PATCH 不是 PUT：只有显式给出的字段才进 payload，
            # 没给的列不动。这里沿用 create 的取值口径。
            up: dict[str, Any] = {"profileId": args.profile_id}
            for key in ("name", "positioning", "audience", "tone"):
                value = getattr(args, key, None)
                if value:
                    up[key] = value
            if getattr(args, "pillars", None):
                up["contentPillars"] = args.pillars
            if getattr(args, "banned", None):
                up["bannedExpressions"] = args.banned
            return up
        if action == "scripts":
            sp: dict[str, Any] = {"profileId": args.profile_id}
            if getattr(args, "keyword", None):
                sp["keyword"] = args.keyword
            return sp
        if action == "script-import":
            content = _script_content(args)
            sip: dict[str, Any] = {"profileId": args.profile_id, "content": content}
            if getattr(args, "title", None):
                sip["title"] = args.title
            if getattr(args, "source", None):
                sip["source"] = args.source
            return sip
        if action == "script-delete":
            return {"scriptId": args.script_id}
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
        if action == "diff":
            vdf: dict[str, Any] = {"versionId": args.version_id}
            if getattr(args, "base_version_id", None):
                vdf["baseVersionId"] = args.base_version_id
            return vdf
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
        if action == "timeline":
            return {"projectId": args.project_id, "format": args.format}
        if action == "fill":
            # ADR-008：这里只产出「内容与约束」，不点最终发布
            fp: dict[str, Any] = {"variantId": args.variant_id}
            if getattr(args, "cover_path", None):
                fp["coverPath"] = args.cover_path
            if getattr(args, "scheduled_at", None):
                fp["scheduledAt"] = args.scheduled_at
            return fp
        if action == "auth-request":
            return {"variantId": args.variant_id}
        if action == "provider":
            # 只探状态：无入参 —— 可用性是**环境**的函数（PATH/daemon/登录态），
            # 不是 payload 的函数
            return {}
        if action == "schedule":
            scp: dict[str, Any] = {
                "variantId": args.variant_id,
                "scheduledAt": args.scheduled_at,
            }
            if getattr(args, "note", None):
                scp["note"] = args.note
            return scp
        if action == "unschedule":
            return {"scheduleId": args.schedule_id}
        if action == "schedules":
            sls: dict[str, Any] = {}
            if getattr(args, "project_id", None):
                sls["projectId"] = args.project_id
            if getattr(args, "status", None):
                sls["status"] = args.status
            return sls
        if action == "fire-due":
            return {}
        raise ValueError(f"unknown publish action: {action!r}")

    if command == "analysis":
        action = getattr(args, "analysis_action", None)
        if action == "save":
            return _analysis_save_payload(args)
        raise ValueError(f"unknown analysis action: {action!r}")

    # ----- S6 第二批新增组（GUI/CLI 一等公民对等） -----

    if command == "plugin":
        action = getattr(args, "plugin_action", None)
        if action == "list":
            return {}
        if action in ("preview", "install"):
            return {"path": args.path}
        if action in ("uninstall", "enable", "disable", "health"):
            return {"pluginId": args.plugin_id}
        raise ValueError(f"unknown plugin action: {action!r}")

    if command == "agent":
        action = getattr(args, "agent_action", None)
        if action in ("connections", "tasks", "artifacts"):
            return {}
        if action == "set-status":
            return {"connectionId": args.connection_id, "status": args.status}
        if action == "disconnect":
            return {"connectionId": args.connection_id}
        raise ValueError(f"unknown agent action: {action!r}")

    if command == "a2a":
        action = getattr(args, "a2a_action", None)
        if action == "add":
            ap: dict[str, Any] = {"url": args.url}
            if getattr(args, "token", None):
                ap["token"] = args.token
            return ap
        if action == "start":
            # port 缺省不写入：让 worker 自己挑端口，别在 CLI 写死默认值
            return {"port": args.port} if args.port is not None else {}
        if action in ("stop", "status"):
            return {}
        raise ValueError(f"unknown a2a action: {action!r}")

    if command == "acp":
        action = getattr(args, "acp_action", None)
        if action == "add":
            return {"command": args.acp_command}
        raise ValueError(f"unknown acp action: {action!r}")

    if command == "approvals":
        action = getattr(args, "approvals_action", None)
        if action == "list":
            alp: dict[str, Any] = {}
            if getattr(args, "status", None):
                alp["status"] = args.status
            if getattr(args, "limit", None) is not None:
                alp["limit"] = args.limit
            return alp
        if action == "decide":
            return {"approvalId": args.approval_id, "decision": args.decision}
        raise ValueError(f"unknown approvals action: {action!r}")

    if command == "diagnostics":
        action = getattr(args, "diagnostics_action", None)
        if action == "export":
            dgp: dict[str, Any] = {}
            # desensitize 默认 None：不指定就跟随配置，别在 CLI 替配置做决定
            if getattr(args, "desensitize", None) is not None:
                dgp["desensitize"] = args.desensitize
            if getattr(args, "max_log_lines", None) is not None:
                dgp["maxLogLines"] = args.max_log_lines
            return dgp
        raise ValueError(f"unknown diagnostics action: {action!r}")

    if command == "provenance":
        action = getattr(args, "provenance_action", None)
        if action == "get":
            # handler 两种命名都收（subjectType / subject_type），取 camelCase
            return {"subjectType": args.subject_type, "subjectId": args.subject_id}
        raise ValueError(f"unknown provenance action: {action!r}")

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
