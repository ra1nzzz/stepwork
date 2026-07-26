"""命令注册一致性测试。

一个新命令必须同时出现在三处，漏一处都是**运行时**才暴露的失败：

- ``bus._ROUTES`` —— 缺了就 ``unknown commandType``
- ``schemas/command-envelope.schema.json`` 的 enum —— 缺了信封校验就拒
- 前端 ``apps/desktop/src/lib/types.ts`` 的 commandType union —— 缺了
  TypeScript 编译不过（但只有跑前端构建才发现）

本测试把这个不变量固化下来，让漏注册在 CI 就红。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _bus_commands() -> set[str]:
    text = (_ROOT / "worker/runtime/commands/bus.py").read_text(encoding="utf-8")
    return set(
        re.findall(r'^\s*"([A-Z][A-Za-z]+)":\s*"worker\.runtime\.handlers', text, re.M)
    )


def _schema_commands() -> set[str]:
    schema = json.loads(
        (_ROOT / "schemas/command-envelope.schema.json").read_text(encoding="utf-8")
    )
    return set(schema["properties"]["commandType"]["enum"])


def _frontend_commands() -> set[str]:
    text = (_ROOT / "apps/desktop/src/lib/types.ts").read_text(encoding="utf-8")
    block = text.split("commandType:")[1].split(";")[0]
    return set(re.findall(r'"([A-Z][A-Za-z]+)"', block))


def test_bus_routes_and_schema_enum_match() -> None:
    bus, schema = _bus_commands(), _schema_commands()
    assert bus, "未能从 bus.py 解析出任何命令（正则失效？）"
    assert not bus - schema, (
        f"有 bus 路由但 schema enum 缺失（信封会被拒）: {sorted(bus - schema)}"
    )
    assert not schema - bus, (
        f"schema enum 有但无 bus 路由（unknown commandType）: {sorted(schema - bus)}"
    )


def test_frontend_union_matches_bus_routes() -> None:
    bus, frontend = _bus_commands(), _frontend_commands()
    assert frontend, "未能从 types.ts 解析出命令 union（结构变了？）"
    assert not bus - frontend, (
        f"后端有但前端 union 缺（前端无法调用）: {sorted(bus - frontend)}"
    )
    assert not frontend - bus, (
        f"前端 union 有但后端无路由（调用必失败）: {sorted(frontend - bus)}"
    )
