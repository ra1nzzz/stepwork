"""从响应契约生成前端 TypeScript 类型与 JSON Schema。

单一事实源是 ``worker/runtime/results/models.py``。生成两份产物：

- ``schemas/results/<CommandType>.schema.json`` —— 跨语言可消费；
- ``apps/desktop/src/lib/results.generated.ts`` —— 前端按 commandType 推导
  ``detail`` 类型，消灭「``as { xxx?: ... }``」这类裸断言。

用法：``python scripts/gen_result_types.py``（``--check`` 只校验是否已同步）。
CI 跑 ``--check``：生成物与源不同步就红，避免有人改了模型忘了重生成。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker.runtime.results.registry import (  # noqa: E402
    RESULT_MODELS,
    export_json_schemas,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas" / "results"
TS_TARGET = ROOT / "apps" / "desktop" / "src" / "lib" / "results.generated.ts"

_HEADER = """/**
 * 由 scripts/gen_result_types.py 从 worker/runtime/results/models.py 生成。
 *
 * 请勿手改：改后端模型后重跑生成脚本。CI 会校验两者同步。
 *
 * 这层类型的意义：此前前端用 `as { xxx?: T }` 裸断言消费 detail，后端改字段名
 * 前端只会静默拿到 undefined —— 没有编译错误、没有测试变红。现在改名会直接
 * 编译不过。
 */

/* eslint-disable */
"""


def _ts_type(schema: dict[str, Any], defs: dict[str, Any]) -> str:
    """JSON Schema 片段 → TS 类型串。

    只覆盖本项目模型实际用到的构造（对象/数组/联合/基础类型 + $ref），
    刻意不做通用 JSON Schema→TS 转换器 —— 那是另一个项目的工作量，
    而且覆盖不到的构造会静默生成 any，比不生成更糟。
    """
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return _ts_type(defs.get(name, {}), defs)

    if "anyOf" in schema:
        parts = [_ts_type(s, defs) for s in schema["anyOf"]]
        return " | ".join(dict.fromkeys(parts))

    kind = schema.get("type")
    if kind == "array":
        return f"{_ts_type(schema.get('items', {}), defs)}[]"
    if kind == "object":
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict):
            return f"Record<string, {_ts_type(extra, defs)}>"
        if extra is True or extra is None:
            return "Record<string, unknown>"
        return "Record<string, never>"
    return {
        "string": "string",
        "integer": "number",
        "number": "number",
        "boolean": "boolean",
        "null": "null",
    }.get(str(kind), "unknown")


def _render_interface(command_type: str, schema: dict[str, Any]) -> str:
    defs = schema.get("$defs", {})
    required = set(schema.get("required", []))
    lines = [f"export interface {command_type}Detail {{"]
    for field, spec in schema.get("properties", {}).items():
        optional = "" if field in required else "?"
        described = spec.get("description")
        if described:
            lines.append(f"  /** {described} */")
        lines.append(f"  {field}{optional}: {_ts_type(spec, defs)};")
    lines.append("}")
    return "\n".join(lines)


def render_ts() -> str:
    blocks: list[str] = [_HEADER]
    entries: list[str] = []
    for command_type, model in sorted(RESULT_MODELS.items()):
        schema = model.model_json_schema()
        blocks.append(_render_interface(command_type, schema))
        entries.append(f'  {command_type}: {command_type}Detail;')

    blocks.append(
        "/**\n"
        " * commandType → detail 类型的映射。dispatchCommandTyped 用它做推导；\n"
        " * 未登记契约的命令回落 Record<string, unknown>（与改造前行为一致）。\n"
        " */\n"
        "export interface CommandResultDetails {\n" + "\n".join(entries) + "\n}"
    )
    blocks.append(
        "/** 已登记响应契约的命令名（运行期可用，测试据此核对覆盖面）。 */\n"
        "export const CONTRACTED_COMMANDS = [\n"
        + "\n".join(f'  "{c}",' for c in sorted(RESULT_MODELS))
        + "\n] as const;"
    )
    return "\n\n".join(blocks) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只校验是否已同步")
    args = parser.parse_args()

    ts = render_ts()
    if args.check:
        if not TS_TARGET.is_file():
            print("results.generated.ts 不存在，请运行 python scripts/gen_result_types.py")
            return 1
        if TS_TARGET.read_text(encoding="utf-8") != ts:
            print("results.generated.ts 与 worker/runtime/results/models.py 不同步")
            print("请运行：python scripts/gen_result_types.py")
            return 1
        for command_type, model in RESULT_MODELS.items():
            target = SCHEMA_DIR / f"{command_type}.schema.json"
            if not target.is_file():
                print(f"缺少 schema：{target}")
                return 1
            expected = model.model_json_schema()
            expected["title"] = f"{command_type}Detail"
            if json.loads(target.read_text(encoding="utf-8")) != expected:
                print(f"schema 不同步：{target}")
                return 1
        print("响应契约生成物已同步")
        return 0

    export_json_schemas(SCHEMA_DIR)
    TS_TARGET.write_text(ts, encoding="utf-8")
    print(f"已生成 {len(RESULT_MODELS)} 条契约 → {SCHEMA_DIR} 与 {TS_TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
