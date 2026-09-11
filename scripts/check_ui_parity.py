"""GUI / CLI 能力对等 + detail 字段消费清单（S6 验收的可执行化）。

回答 S6 验收清单的第一条：**「GUI 能做但 CLI 做不到」的能力数为 0。**

判定不靠人工维护一张清单（那种清单一定会和代码脱节），而是解析**真实入口**
与**真实调用点**：

============  ==========================================================
来源          怎么取
============  ==========================================================
权威路由      ``worker/runtime/commands/bus.py`` 的 ``_ROUTES``
CLI 入口      ``cli/__main__.py`` 的 ``set_defaults(command_type="…")``
GUI 调用点    ``apps/desktop/src`` 下 ``buildEnvelope`` / ``runCommand`` /
              ``useCommand`` / ``useCommandMutation`` 的**字面量**首参
detail 字段   ``worker/runtime/results/models.py`` 的 ``*Detail`` 定义
============  ==========================================================

「同源」是结构性保证——GUI 与 CLI 最终都落到同一个 ``dispatch()``。所以对等性
**不需要比对两端输出**，只需要证明**两端都能到达同一个 commandType**。

检查项与处理
------------

============  =======================================  ==========
检查          含义                                     不通过时
============  =======================================  ==========
A. CLI⊆bus    CLI 指向不存在的命令（拼写错）            硬失败
B. GUI⊆bus    前端调了后端没有的命令                    硬失败
C. GUI⊆CLI    **S6 那条**：GUI 能做但 CLI 做不到        只许减不许增
D. 必需字段   ``_REQUIRED_UI_FIELDS`` 登记的字段要在前端有消费点  硬失败
报告          动态调用点 / 未消费字段清单                仅打印
============  =======================================  ==========

C 目前**不是**零——那是已知欠账，用 ``_KNOWN_GAP`` 冻结基线，**只许减不许增**：
每补一个 CLI 子命令就从 ``_KNOWN_GAP`` 里删一个；补完即真正归零。冻结而不是
直接硬失败，是为了让这道门禁**今天就能进 CI**（允许还债，禁止添债）。

字段消费那一项（D）是**按名字**在前端源码里找的，有假阳性（``count`` /
``tool`` 这类通用名会在无关位置命中）。所以它只对 ``_REQUIRED_UI_FIELDS``
**显式登记**的字段硬失败，「未消费清单」当线索看，不要当判决书。

与 ``worker/tests/test_command_registry.py`` 的分工：那边管「三处注册一致」
（bus / schema / types.ts），这边管「GUI 与 CLI 都能到达」。bus 的解析正则与
那边同源，改了解析方式两处要一起改。

用法（仓库根）::

    python scripts/check_ui_parity.py
    python scripts/check_ui_parity.py --fields      # 额外打印全部字段消费清单
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Final

_ROOT = Path(__file__).resolve().parents[1]
_GUI_DIR: Final = _ROOT / "apps/desktop/src"

#: 「前端必须有消费点」的字段。带语义注释的契约字段，UI 不消费就是白写。
#:
#: 只登记**已经在 UI 里有真实消费点**的字段（读契约的地方集中在
#: `features/hotspots/viewModel.ts`），不做「先登记后实现」——那会让门禁长期红着，
#: 红了就会被绕过。补一批 UI 就加一批。
_REQUIRED_UI_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "RecommendHotspotsDetail": (
        "recommendations",
        "count",
        "considered",
        "reason_source",
        "brand_applied",
        "reason_note",
    ),
    "ConvertHotspotToTopicDetail": (
        "trust_level",
        "review_state",
        "reason_source",
        "reused",
        "brief",
        "next_step",
    ),
    "ListHotspotSourcesDetail": (
        "sources",
        "server_marker",
    ),
    "DiscoverHotspotsDetail": (
        "batch_id",
        "count",
        "saved",
        "sources",
        "errors",
        "skipped",
    ),
}

#: 已知欠账：GUI 可达但 CLI 尚无入口的命令（S6 验收项未达成）。**只许减不许增**。
#:
#: 2026-09-12 冻结基线 —— 实测 38 条，比原以为的「就差一个 mcp」大得多：插件、
#: Agent 连接、审批、定时发布、品牌脚本、项目导入导出整片都只有 GUI 路径。
#: 冻结而不是当场硬失败，是为了让这道门禁**今天就能进 CI**：允许还债，禁止添债。
#: 每补一个 CLI 子命令，就从这里删一个；删空即 S6 验收项真正达成。
_KNOWN_GAP: Final[frozenset[str]] = frozenset(
    {
        "AddA2aAgent",
        "AddAcpAgent",
        "BuildPlatformFillPackage",
        "CancelScheduledPublish",
        "CheckPluginHealth",
        "DecideApprovalRequest",
        "DeleteAgentConnection",
        "DeleteAsset",
        "DeleteBrandScript",
        "DiffContentVersions",
        "DisablePlugin",
        "EnablePlugin",
        "ExportDiagnosticsBundle",
        "ExportEditTimeline",
        "ExportProject",
        "FireDueSchedules",
        "GetA2aServerStatus",
        "GetConfig",
        "GetProvenance",
        "ImportBrandScript",
        "ImportProject",
        "InstallPlugin",
        "ListAgentArtifacts",
        "ListAgentConnections",
        "ListAgentTasks",
        "ListApprovalRequests",
        "ListBrandScripts",
        "ListPlugins",
        "ListScheduledPublishes",
        "PreviewPluginManifest",
        "RequestPublishAuthorization",
        "SchedulePublish",
        "SetAgentConnectionStatus",
        "StartA2aServer",
        "StopA2aServer",
        "UninstallPlugin",
        "UpdateBrandProfile",
        "UpdateConfig",
    }
)

#: bus 的路由行。与 worker/tests/test_command_registry.py 同源。
#: 命令名允许含数字：A2A 这类协议名本身带数字（StartA2aServer）。
_BUS_RE: Final = re.compile(
    r'^\s*"([A-Z][A-Za-z0-9]+)":\s*"worker\.runtime\.handlers', re.M
)

#: CLI 的命令映射：每个子命令 set_defaults(command_type="X")。
_CLI_RE: Final = re.compile(r'set_defaults\(\s*command_type="([A-Za-z0-9]+)"')

#: GUI 调用点：首参是**字面量**的四种入口。
_GUI_CALL_RE: Final = re.compile(
    r'\b(?:buildEnvelope|runCommand|useCommand|useCommandMutation)\(\s*"([A-Z][A-Za-z0-9]*)"'
)

#: 首参**是标识符**（变量）的调用点——静态解析不了，用于报告，不参与对等判定。
#:
#: 必须是「标识符」而不是「非引号」：写成 ``\(\s*(?!")`` 会把所有**跨行的
#: 字面量调用**（``runCommand(\n  "RecommendHotspots"``）也误报成动态，因为
#: ``\s*`` 可以零宽匹配、紧随其后的换行当然不是引号。那种假报告会让人不再看
#: 这一节，报告等于失效。
_GUI_DYNAMIC_RE: Final = re.compile(
    r"\b(?:buildEnvelope|runCommand|useCommand|useCommandMutation)\(\s*([A-Za-z_$][\w$.]*)"
)

#: 动态调用点里常见的一跳：``const commandType = enable ? "EnablePlugin" : "DisablePlugin"``。
#: 把这层别名解开，否则最典型的「按行 action」会被整片漏掉。
_GUI_ALIAS_RE: Final = re.compile(
    r"(?:const|let)\s+(?:commandType|command)\s*=\s*([^;]+);"
)

#: 管道自身（不是业务调用点）。
_GUI_PLUMBING: Final = frozenset({"lib/useCommand.ts", "lib/tauri.ts"})

#: 生成物与纯类型面：命中它们不算「被 UI 消费」（它们本来就是契约的镜像）。
_FIELD_SCAN_EXCLUDE: Final = frozenset({"lib/results.generated.ts", "lib/types.ts"})

_DETAIL_CLASS_RE: Final = re.compile(r"^class\s+(\w+Detail)\(ResultModel\):", re.M)
_FIELD_LINE_RE: Final = re.compile(r"^ {4}([a-z_][a-z0-9_]*)\s*:", re.M)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _bus_commands() -> set[str]:
    return set(_BUS_RE.findall(_read(_ROOT / "worker/runtime/commands/bus.py")))


def _cli_commands() -> set[str]:
    return set(_CLI_RE.findall(_read(_ROOT / "cli/__main__.py")))


def _gui_sources() -> list[Path]:
    """业务源码（排除测试与生成物）。测试里的命令字面量不算产品能力面。"""
    return [
        p
        for suffix in ("*.ts", "*.tsx")
        for p in _GUI_DIR.rglob(suffix)
        if ".test." not in p.name
    ]


def _gui_commands() -> tuple[set[str], list[str]]:
    """返回（GUI 可达命令集, 解析不了的动态调用点）。

    动态调用点单独返回而不是静默丢弃——否则这个脚本会「绿得没有依据」。
    """
    found: set[str] = set()
    dynamic: list[str] = []
    for path in _gui_sources():
        rel = path.relative_to(_GUI_DIR).as_posix()
        text = _read(path)
        found.update(_GUI_CALL_RE.findall(text))
        for rhs in _GUI_ALIAS_RE.findall(text):
            found.update(re.findall(r'"([A-Z][A-Za-z0-9]*)"', rhs))
        if rel in _GUI_PLUMBING:
            continue
        for match in _GUI_DYNAMIC_RE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            dynamic.append(f"{rel}:{line}")
    return found, dynamic


def _detail_fields() -> dict[str, list[str]]:
    """``models.py`` 里每个 ``*Detail`` 的字段名（按定义顺序）。"""
    text = _read(_ROOT / "worker/runtime/results/models.py")
    out: dict[str, list[str]] = {}
    hits = list(_DETAIL_CLASS_RE.finditer(text))
    for i, match in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        body = text[match.end() : end]
        out[match.group(1)] = _FIELD_LINE_RE.findall(body)
    return out


def _ui_field_usage() -> dict[str, int]:
    """前端源码里每个字段名的出现次数（生成物与纯类型面不计）。"""
    counts: dict[str, int] = {}
    for path in _gui_sources():
        if path.relative_to(_GUI_DIR).as_posix() in _FIELD_SCAN_EXCLUDE:
            continue
        for name in re.findall(r"\b([a-z_][a-z0-9_]*)\b", _read(path)):
            counts[name] = counts.get(name, 0) + 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description="GUI / CLI 能力对等 + detail 字段消费清单")
    ap.add_argument("--fields", action="store_true", help="打印全部字段消费清单（不止未消费的）")
    args = ap.parse_args()

    failures: list[str] = []

    bus = _bus_commands()
    if not bus:
        print("FAIL: 未能从 bus.py 解析出任何路由（正则失效？改解析方式时两处一起改）")
        return 2
    cli = _cli_commands()
    gui, dynamic = _gui_commands()
    # 解析集为空必须当场失败，不能让它「差集为空 → 检查通过」——
    # 那是这道门禁唯一会**静默变绿**的失效方式，比报错危险得多。
    if not cli:
        print("FAIL: 未能从 cli/__main__.py 解析出任何 command_type 映射（正则失效？）")
        return 2
    if not gui:
        print("FAIL: 未能解析出任何前端命令调用点（解析失效会让本检查静默变绿）")
        return 2

    print(f"权威路由 bus   {len(bus)} 条")
    print(f"CLI 入口       {len(cli)} 条")
    print(f"GUI 可达       {len(gui)} 条\n")

    # ---- A. CLI 指向的命令必须真实存在（多为拼写错） ----
    ghost = sorted(cli - bus)
    if ghost:
        failures.append(f"A. CLI 指向不存在的命令（unknown commandType）: {ghost}")

    # ---- B. 前端调了后端没有的命令 ----
    unknown = sorted(gui - bus)
    if unknown:
        failures.append(f"B. GUI 调了无路由的命令（调用必失败）: {unknown}")

    # ---- C. S6：GUI 能做但 CLI 做不到 ----
    gap = gui - cli
    new_debt = sorted(gap - _KNOWN_GAP)
    repaid = sorted(_KNOWN_GAP - gap)
    total = len(gap) + len(repaid)
    print(f"[C] GUI−CLI 缺口 {len(gap)} 条（已还清 {len(repaid)}/{total}）")
    for name in sorted(gap):
        mark = "欠账" if name in _KNOWN_GAP else "新增"
        print(f"    [{mark}] {name}")
    if repaid:
        print(f"    本次已还清: {repaid}（请从 _KNOWN_GAP 删除对应项）")
    if new_debt:
        failures.append(f"C. 新增 GUI−CLI 缺口（禁止添债，只许还债）: {new_debt}")

    # ---- 报告：解析不了的动态调用点 ----
    if dynamic:
        print(f"\n[报告] 动态调用点 {len(dynamic)} 处（首参是变量，静态解析不了）:")
        for loc in dynamic:
            print(f"    {loc}")

    # ---- D. 必需字段必须有 UI 消费点 ----
    fields = _detail_fields()
    usage = _ui_field_usage()
    missing_required: list[str] = []
    unconsumed: dict[str, list[str]] = {}
    for cls, names in fields.items():
        need = _REQUIRED_UI_FIELDS.get(cls, ())
        dead = [n for n in names if usage.get(n, 0) == 0]
        for name in dead:
            if name in need:
                missing_required.append(f"{cls}.{name}")
        # 只在至少消费了一个字段时才谈「未消费」——整个契约都没接的面板另说
        consumed = [n for n in names if usage.get(n, 0) > 0]
        if consumed and dead:
            unconsumed[cls] = dead

    if missing_required:
        failures.append(f"D. 必需字段无 UI 消费点: {missing_required}")

    if unconsumed:
        print("\n[报告] 契约已接入但字段未消费（契约写了却没用的字段）:")
        for cls, dead in sorted(unconsumed.items()):
            print(f"    {cls}: {dead}")
    if args.fields:
        print("\n[报告] 全部 detail 字段消费清单:")
        for cls, names in sorted(fields.items()):
            marks = ", ".join(f"{n}={'✓' if usage.get(n, 0) else '✗'}" for n in names)
            print(f"    {cls}: {marks}")

    print()
    if failures:
        for line in failures:
            print(f"FAIL {line}")
        return 1
    print("PASS GUI/CLI 能力对等检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
