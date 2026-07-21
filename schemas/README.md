# STEPWORK Schemas

本目录是 STEPWORK 所有 **JSON Schema 的唯一事实源**（Single Source of Truth）。任何 Rust / Python / TypeScript 实现必须从这里读取 Schema，**禁止在其他位置维护副本**。

所有 Schema 遵循 [JSON Schema Draft 2020-12](https://json-schema.org/draft/2020-12/schema)。

## 文件清单

| 文件 | 说明 | 规格来源 |
|---|---|---|
| `command-envelope.schema.json` | Command Envelope v1，所有进入 Application Services 的请求 | SYSTEM_SPEC §7.1 |
| `artifact-envelope.schema.json` | Artifact Envelope v1，所有离散产出物 | SYSTEM_SPEC §11.1 |
| `job-state.enum.json` | Job 主状态枚举（9 个） | SYSTEM_SPEC §10.1 |
| `job-stage.enum.json` | Job 业务阶段枚举（9 个） | SYSTEM_SPEC §10.1 |
| `error-envelope.schema.json` | 统一错误信封 | SYSTEM_SPEC §16 |

## 使用方式

### Rust（`apps/desktop/src-tauri/`）

使用 `include_str!` 在编译时嵌入 Schema 字符串：

```rust
use jsonschema::JSONSchema;

const COMMAND_ENVELOPE_SCHEMA: &str = include_str!("../../../schemas/command-envelope.schema.json");

pub fn validate_command(payload: &serde_json::Value) -> Result<(), Vec<String>> {
    let schema_json: serde_json::Value = serde_json::from_str(COMMAND_ENVELOPE_SCHEMA)?;
    let compiled = JSONSchema::compile(&schema_json)?;
    match compiled.validate(payload) {
        Ok(_) => Ok(()),
        Err(errors) => Err(errors.map(|e| e.to_string()).collect()),
    }
}
```

构建系统（`build.rs`）也可以将 Schema 复制到 `OUT_DIR` 后再 `include!`。

### Python（`worker/`）

使用 `importlib.resources` 从包内读取（Schema 在打包时需复制为 package data）：

```python
import json
from importlib import resources
import jsonschema

def load_schema(name: str) -> dict:
    # 假设构建时将 schemas/ 复制为 worker/_schemas/ 包数据
    with resources.files("worker._schemas").join_text(name).open("r", encoding="utf-8") as f:
        return json.load(f)

COMMAND_ENVELOPE = load_schema("command-envelope.schema.json")

def validate_command(payload: dict) -> None:
    jsonschema.validate(instance=payload, schema=COMMAND_ENVELOPE)
```

开发环境直接读取仓库根 `schemas/` 目录（相对路径）：

```python
from pathlib import Path
import json

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "schemas"

with open(SCHEMA_DIR / "command-envelope.schema.json", "r", encoding="utf-8") as f:
    COMMAND_ENVELOPE = json.load(f)
```

### TypeScript（`apps/desktop/src/`）

前端 Schema 校验使用 [Zod](https://zod.dev) 或 [Ajv](https://ajv.js.org)。Schema 文件通过 Vite 的 `?raw` 导入：

```typescript
import commandEnvelopeSchema from "../../../schemas/command-envelope.schema.json?raw";
import Ajv from "ajv";

const ajv = new Ajv();
const validate = ajv.compile(JSON.parse(commandEnvelopeSchema));

export function validateCommand(payload: unknown): boolean {
  return validate(payload);
}
```

## 修改 Schema 的规则

1. **Schema 是公开契约**：修改任何字段都会破坏向后兼容
2. **新增可选字段**：允许，直接改文件，版本号不变
3. **新增必填字段 / 修改字段类型 / 删除字段**：**禁止**。必须新建 `*-v2.schema.json` 并同步更新代码
4. **修改枚举值**：只允许新增枚举项，不允许删除或重命名
5. **所有变更必须经过 PR + ADR**：在 `docs/adr/` 中记录 Schema 演进决策

## 测试

每个 Schema 必须附带至少一个**正例**和一个**反例**，存放在 `tests/schemas/`：

```
tests/schemas/
├── command-envelope/
│   ├── valid-001.json
│   ├── invalid-missing-actor.json
│   └── invalid-bad-source-enum.json
├── artifact-envelope/
│   ├── valid-001.json
│   └── ...
└── ...
```

CI 中使用 `jsonschema` (Python) 或 `ajv` (Node) 自动校验所有正例通过、所有反例失败。

## 引用关系

- `core/schemas/README.md` 说明本目录为唯一源（参见 W1_MONOREPO_PLAN v1.1 Patch-A1）
- `migrations/` 中的 SQL 字段（如 `jobs.state`）对应 `job-state.enum.json` 的枚举值
- `worker/` 与 `apps/desktop/src-tauri/` 在构建时从这里读取 Schema
