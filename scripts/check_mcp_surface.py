"""MCP 工具面与命令总线的一致性门禁（S6 验收的可执行化）。

回答 S6 验收清单的第四条：**「MCP 工具清单与命令总线自动同步」。**

MCP 是第四个「入口」（CLI / GUI / A2A / ACP 之外），但它比另外三个多担一条
**安全边界**：只暴露只读工具，且 ``update_config`` / ``UpdateConfig`` 永不注册
—— 密钥不可能经 MCP 写入。此前这条保证只写在 ``mcp/server.py`` 的注释与模块
docstring 里，是**承诺**；本脚本把它变成**可执行的事实**。

判定不靠人工维护清单，而是解析五处真实定义：

==================  ==========================================================
来源                怎么取
==================  ==========================================================
MCP 工具映射        ``mcp/server.py`` 的 ``_TOOL_COMMANDS``（tool → commandType）
MCP 工具目录        ``mcp/server.py`` 的 ``TOOLS``（name + inputSchema）
MCP 参数翻译        ``mcp/server.py`` 的 ``_build_payload``（读了哪些 argument 键）
权威路由            ``worker/runtime/commands/bus.py`` 的 ``_ROUTES``
agent 只读白名单    bus.py 的 ``_AGENT_ALLOWED_COMMANDS`` / ``_AGENT_SOURCES``
                    / ``_AGENT_ACTOR_TYPES`` / ``_ALLOWED_CONFIG_ACTORS``
==================  ==========================================================

为什么这里用 ``ast`` 而隔壁 ``check_ui_parity.py`` 用正则：那边的「事实」一半
在 TS 源码里，跨语言只能用正则；这边的「事实」本身就是 Python 数据字面量
（dict / list / 函数体），解析 AST 既准确又能拿到结构（哪个参数属于哪个工具）。

检查项与处理
------------

=================  ================================================  ==========
检查               含义                                              不通过时
=================  ================================================  ==========
A. 解析非空        六处定义都解析出了东西（改名/换写法就当场失败）     硬失败
B. MCP⊆bus         MCP 指向不存在的命令（拼写错必死）                 硬失败
C. 目录自洽        ``TOOLS`` 与 ``_TOOL_COMMANDS`` 是同一批名字        硬失败
D. MCP⊆只读        MCP 暴露的命令必须在 agent 只读白名单内            硬失败
E. 密钥边界        两个禁用名在 MCP 面与配置白名单里永不出现          硬失败
F. schema 自洽     ``type: object`` / ``required ⊆ properties``       硬失败
G. 参数不被吞      声明的 property 必须真被 ``_build_payload`` 读       硬失败
H. 无幽灵参数      ``_build_payload`` 读的键必须被声明过               硬失败
=================  ================================================  ==========

C 是本门禁最有价值的一条：两个方向的漂移都会让 Agent 侧出现**说不清的坏掉**——
只写 ``_TOOL_COMMANDS`` 是「工具存在但 ``tools/list`` 里看不到」，只写 ``TOOLS``
是「``tools/list`` 广告了一个调用即 ``unknown tool`` 的工具」。两种都不会报错，
只会让调用方迷惑。

G / H 是**函数级**判定，不是逐工具的 if 分支级：``_build_payload`` 是一条 if 链，
静态切分支会因 fall-through 产生假报告，而假报告会让人不再信任这道门禁。逐工具
的精确版在 ``mcp/tests/test_mcp.py`` 里（那里执行真的 ``_build_payload``）。

用法（仓库根）::

    python scripts/check_mcp_surface.py
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Final, NamedTuple, TypeVar

_ROOT = Path(__file__).resolve().parents[1]
_MCP_SERVER: Final = _ROOT / "mcp/server.py"
_BUS: Final = _ROOT / "worker/runtime/commands/bus.py"

#: ``_build_payload`` 的形参名。三种读法都锚在它上面。
_ARGS_NAME: Final = "arguments"

#: 「密钥绝不能经 MCP 写入」的两个名字：工具名（连字符风格）与 bus 的 commandType。
_FORBIDDEN_TOOL: Final = "update_config"
_FORBIDDEN_COMMAND: Final = "UpdateConfig"

_T = TypeVar("_T")


class ToolShape(NamedTuple):
    """一个 MCP 工具的 inputSchema 形状（只取门禁要判的部分）。"""

    properties: tuple[str, ...]
    required: tuple[str, ...]
    untyped_props: tuple[str, ...]
    schema_type: str | None


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _tree(path: Path) -> ast.Module:
    return ast.parse(_read(path), filename=str(path))


def _assigned(tree: ast.Module, name: str) -> ast.expr:
    """取模块级 ``name = <expr>`` 的右值（``AnnAssign`` 也算）。

    Raises:
        LookupError: 没找到。**必须抛而不是返回空** —— 静默返回空集合会让
            后面所有差集检查「因为没有差集而通过」，那是本门禁唯一会静默变绿的
            失效方式，比报错危险得多。
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                return node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                if node.value is None:
                    raise LookupError(f"{name} 只有注解没有值")
                return node.value
    raise LookupError(name)


def _dict_literal(node: ast.expr, what: str) -> dict[str, ast.expr]:
    """``{"k": <expr>, ...}`` → ``{"k": <expr>}``（值原样留着按需解释）。"""
    if not isinstance(node, ast.Dict):
        raise TypeError(f"{what} 不是 dict 字面量（实际 {type(node).__name__}）")
    out: dict[str, ast.expr] = {}
    for key, value in zip(node.keys, node.values, strict=True):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            out[key.value] = value
    return out


def _str_of(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _str_dict(node: ast.expr, what: str) -> dict[str, str]:
    """``{"a": "b"}`` → ``{"a": "b"}``；值不是字符串字面量时记空串。"""
    return {k: (_str_of(v) or "") for k, v in _dict_literal(node, what).items()}


def _str_collection(node: ast.expr, what: str) -> set[str]:
    """``{...}`` / ``[...]`` / ``(...)`` / ``frozenset({...})`` → 字符串集合。"""
    if isinstance(node, ast.Call):
        if not node.args:
            raise TypeError(f"{what} 是个空调用，取不到集合")
        return _str_collection(node.args[0], what)
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        out: set[str] = set()
        for element in node.elts:
            text = _str_of(element)
            if text is not None:
                out.add(text)
        return out
    raise TypeError(f"{what} 不是集合字面量（实际 {type(node).__name__}）")


def _tool_catalogue(node: ast.expr) -> dict[str, ToolShape]:
    """``TOOLS`` 字面量 → ``{tool name: ToolShape}``。"""
    if not isinstance(node, ast.List):
        raise TypeError(f"TOOLS 不是 list 字面量（实际 {type(node).__name__}）")
    out: dict[str, ToolShape] = {}
    for entry in node.elts:
        if not isinstance(entry, ast.Dict):
            raise TypeError("TOOLS 里出现了非 dict 条目")
        fields = _dict_literal(entry, "TOOLS 条目")
        name = _str_of(fields.get("name"))
        if name is None:
            raise ValueError("TOOLS 条目缺少字符串 name")
        schema = fields.get("inputSchema")
        if schema is None:
            raise ValueError(f"工具 {name} 缺少 inputSchema")
        schema_fields = _dict_literal(schema, f"{name}.inputSchema")

        properties: tuple[str, ...] = ()
        untyped: list[str] = []
        props_node = schema_fields.get("properties")
        if props_node is not None:
            prop_map = _dict_literal(props_node, f"{name}.inputSchema.properties")
            properties = tuple(prop_map)
            untyped = [
                p
                for p, spec in prop_map.items()
                if _str_of(_dict_literal(spec, f"{name}.{p}").get("type")) is None
            ]

        required: tuple[str, ...] = ()
        required_node = schema_fields.get("required")
        if isinstance(required_node, (ast.List, ast.Tuple)):
            required = tuple(
                text
                for text in (_str_of(e) for e in required_node.elts)
                if text is not None
            )

        out[name] = ToolShape(
            properties=properties,
            required=required,
            untyped_props=tuple(untyped),
            schema_type=_str_of(schema_fields.get("type")),
        )
    return out


def _loop_iter_strings(node: ast.For) -> list[str]:
    """``for key in ("a", "b")`` → ``["a", "b"]``（只认字面量元组/列表）。"""
    if isinstance(node.iter, (ast.Tuple, ast.List)):
        return [
            text for text in (_str_of(e) for e in node.iter.elts) if text is not None
        ]
    return []


def _loop_touches_args(node: ast.For) -> bool:
    return any(
        isinstance(x, ast.Name) and x.id == _ARGS_NAME
        for stmt in [*node.body, *node.orelse]
        for x in ast.walk(stmt)
    )


def _consumed_argument_keys(tree: ast.Module, func_name: str) -> set[str]:
    """``_build_payload`` 真正读了哪些 argument 键。

    三种读法都要认，漏一种就会出假报告：

    * ``arguments.get("x")`` / ``arguments["x"]``
    * ``for key in ("a", "b"): … arguments.get(key)`` —— 循环元组里的字面量，
      函数体里**只出现变量名**，按「``.get(`` 后跟字面量」扫的写法会整片漏掉，
      于是把合法的参数误报成「声明了但从不读取」。

    Raises:
        LookupError: 找不到该函数（改名了）。
    """
    func = next(
        (
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == func_name
        ),
        None,
    )
    if func is None:
        raise LookupError(func_name)

    consumed: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if not node.args:
                continue
            base = node.func.value
            if isinstance(base, ast.Name) and base.id == _ARGS_NAME:
                text = _str_of(node.args[0])
                if text is not None:
                    consumed.add(text)
        elif isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Name) and node.value.id == _ARGS_NAME:
                text = _str_of(node.slice)
                if text is not None:
                    consumed.add(text)
        elif isinstance(node, ast.For) and _loop_touches_args(node):
            consumed.update(_loop_iter_strings(node))
    return consumed


class Facts(NamedTuple):
    """一次解析得到的全部事实（六处定义）。"""

    routes: set[str]
    allowlist: set[str]
    agent_sources: set[str]
    agent_actors: set[str]
    config_actors: set[str]
    tool_commands: dict[str, str]
    tools: dict[str, ToolShape]
    consumed: set[str]


def _parse_all() -> tuple[Facts | None, list[str]]:
    """解析全部事实。返回 ``(事实, 坏掉的对象说明)``，任一坏掉则事实为 ``None``。

    单条失败不立刻抛出：要**一次列全**所有坏掉的对象，而不是改一个跑一次。
    这里兜住 LookupError/TypeError/ValueError —— 它们是「被解析的对象改名或换了
    写法」，属于本门禁要报的错，不该以 traceback 的形式冒出去（那会被误读成
    「脚本自己坏了」，而且不会告诉你要改哪个对象）。
    """
    broken: list[str] = []
    bus_tree = _tree(_BUS)
    mcp_tree = _tree(_MCP_SERVER)

    def load(key: str, thunk: Callable[[], _T], fallback: _T) -> _T:
        try:
            return thunk()
        except (LookupError, TypeError, ValueError) as exc:
            broken.append(f"{key} —— {type(exc).__name__}: {exc}")
            return fallback

    facts = Facts(
        routes=load(
            "bus._ROUTES",
            lambda: set(_str_dict(_assigned(bus_tree, "_ROUTES"), "_ROUTES")),
            set(),
        ),
        allowlist=load(
            "bus._AGENT_ALLOWED_COMMANDS",
            lambda: _str_collection(
                _assigned(bus_tree, "_AGENT_ALLOWED_COMMANDS"),
                "_AGENT_ALLOWED_COMMANDS",
            ),
            set(),
        ),
        agent_sources=load(
            "bus._AGENT_SOURCES",
            lambda: _str_collection(
                _assigned(bus_tree, "_AGENT_SOURCES"), "_AGENT_SOURCES"
            ),
            set(),
        ),
        agent_actors=load(
            "bus._AGENT_ACTOR_TYPES",
            lambda: _str_collection(
                _assigned(bus_tree, "_AGENT_ACTOR_TYPES"), "_AGENT_ACTOR_TYPES"
            ),
            set(),
        ),
        config_actors=load(
            "bus._ALLOWED_CONFIG_ACTORS",
            lambda: _str_collection(
                _assigned(bus_tree, "_ALLOWED_CONFIG_ACTORS"), "_ALLOWED_CONFIG_ACTORS"
            ),
            set(),
        ),
        tool_commands=load(
            "mcp._TOOL_COMMANDS",
            lambda: _str_dict(_assigned(mcp_tree, "_TOOL_COMMANDS"), "_TOOL_COMMANDS"),
            {},
        ),
        tools=load(
            "mcp.TOOLS", lambda: _tool_catalogue(_assigned(mcp_tree, "TOOLS")), {}
        ),
        consumed=load(
            "mcp._build_payload",
            lambda: _consumed_argument_keys(mcp_tree, "_build_payload"),
            set(),
        ),
    )
    return (None, broken) if broken else (facts, broken)


def main() -> int:
    failures: list[str] = []

    facts, broken = _parse_all()
    if facts is None:
        # 解析失败必须**说得明白**再退出，不能抛 traceback：traceback 会被当成
        # 「脚本自己坏了」而不是「你改坏了被解析的对象」，也不会告诉你要改哪个。
        print("FAIL A. 以下定义解析失败（改名/换了写法？解析失效会让本门禁静默变绿）:")
        for line in broken:
            print(f"     - {line}")
        return 2

    routes = facts.routes
    allowlist = facts.allowlist
    agent_sources = facts.agent_sources
    agent_actors = facts.agent_actors
    config_actors = facts.config_actors
    tool_commands = facts.tool_commands
    tools = facts.tools
    consumed = facts.consumed

    # ---- A（后半）. 解析到了但集合为空 ----
    # 「解析出空集」与「解析失败」等价：差集全空 → 检查全过。这是本门禁唯一
    # 会静默变绿的失效方式，比报错危险得多，宁可误报也不许漏报。
    empty = [
        name
        for name, value in (
            ("bus._ROUTES", routes),
            ("bus._AGENT_ALLOWED_COMMANDS", allowlist),
            ("bus._AGENT_SOURCES", agent_sources),
            ("mcp._TOOL_COMMANDS", tool_commands),
            ("mcp.TOOLS", tools),
            ("mcp._build_payload 读取的键", consumed),
        )
        if not value
    ]
    if empty:
        for name in empty:
            print(f"FAIL A. 解析结果为空: {name}（定义还在，但一个字面量都没取到）")
        return 2

    print(f"权威路由 bus         {len(routes)} 条")
    print(f"agent 只读白名单     {len(allowlist)} 条")
    print(f"MCP 工具映射         {len(tool_commands)} 条")
    print(f"MCP 工具目录 TOOLS   {len(tools)} 个")
    print(f"_build_payload 读键  {sorted(consumed)}\n")

    exposed = set(tool_commands.values())
    declared = set(tool_commands)
    listed = set(tools)

    # ---- B. MCP 指向的命令必须真实存在（多为拼写错） ----
    ghost = sorted(exposed - routes)
    if ghost:
        failures.append(f"B. MCP 指向无路由的命令（调用必失败）: {ghost}")

    # ---- C. 目录自洽：两个方向的漂移都不会报错，只会让调用方迷惑 ----
    invisible = sorted(declared - listed)
    if invisible:
        failures.append(
            f"C. 已注册但未在 tools/list 宣告（Agent 根本看不到）: {invisible}"
        )
    uncallable = sorted(listed - declared)
    if uncallable:
        failures.append(
            f"C. tools/list 宣告了但 tools/call 会答 unknown tool: {uncallable}"
        )

    # ---- D. 只读边界：MCP 暴露的命令必须在 agent 白名单内 ----
    over = sorted(exposed - allowlist)
    if over:
        failures.append(f"D. MCP 暴露了只读白名单之外的命令: {over}")

    # ---- E. 密钥边界 ----
    # 这条此前只是一句注释（「根授权保证」）。注释不是护栏：有人加一个
    # update_config 工具，注释一个字都不会变。这里把它变成代码事实。
    leak: list[str] = []
    if _FORBIDDEN_TOOL in listed:
        leak.append(f"tools/list 宣告了 {_FORBIDDEN_TOOL!r}")
    if _FORBIDDEN_TOOL in declared:
        leak.append(f"_TOOL_COMMANDS 注册了 {_FORBIDDEN_TOOL!r}")
    if _FORBIDDEN_COMMAND in exposed:
        leak.append(f"有工具映射到 {_FORBIDDEN_COMMAND!r}")
    if _FORBIDDEN_COMMAND in allowlist:
        leak.append(f"{_FORBIDDEN_COMMAND} 混进了 bus 的 agent 白名单")
    bad_actors = sorted(config_actors & (agent_sources | agent_actors))
    if bad_actors:
        leak.append(f"_ALLOWED_CONFIG_ACTORS 含 agent 特征值 {bad_actors}")
    if leak:
        failures.append(f"E. 密钥边界被破（MCP 只读保证失效）: {leak}")

    # ---- F. inputSchema 自洽 ----
    schema_bad: list[str] = []
    for name, shape in sorted(tools.items()):
        if shape.schema_type != "object":
            schema_bad.append(f"{name}.type={shape.schema_type!r}（应为 'object'）")
        extra = [r for r in shape.required if r not in shape.properties]
        if extra:
            schema_bad.append(f"{name}.required 含未声明参数 {extra}")
        if shape.untyped_props:
            schema_bad.append(f"{name}.properties 缺 type {list(shape.untyped_props)}")
    if schema_bad:
        failures.append(f"F. inputSchema 不自洽: {schema_bad}")

    # ---- G / H. 参数面与 _build_payload 对得上 ----
    declared_props = {p for shape in tools.values() for p in shape.properties}
    swallowed = sorted(declared_props - consumed)
    if swallowed:
        failures.append(
            f"G. 工具声明了但 _build_payload 从不读取的参数（Agent 传了也没用）: {swallowed}"
        )
    phantom = sorted(consumed - declared_props)
    if phantom:
        failures.append(
            f"H. _build_payload 读了未声明的参数（Agent 无法提供）: {phantom}"
        )

    print("MCP 工具 → commandType（★ = 白名单内）")
    for name in sorted(tools):
        command = tool_commands.get(name, "<未注册>")
        mark = "★" if command in allowlist else "!"
        args = ", ".join(tools[name].properties) or "-"
        print(f"    {mark} {name:<24} {command:<22} ({args})")

    print()
    if failures:
        for line in failures:
            print(f"FAIL {line}")
        return 1
    print("PASS MCP 工具面与命令总线一致性检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
