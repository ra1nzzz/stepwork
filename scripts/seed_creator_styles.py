"""把 Creator Style 预置导入为 STEPWORK BrandProfile（S4 集成）。

两个主播风格（金枪大叔 / 一勾Ego）的六维 Creator DNA 与禁用词，由观雅集
上对应的 creator skill 蒸馏而来（本机已安装：
``~/.workbuddy/skills/douyin-jinqiang-creator`` /
``~/.workbuddy/skills/douyin-ego-creator``），预置数据在
``scripts/creator_style_presets.json``。

用途：在 STEPWORK 里让「文案生成」能直接选这两种成熟口播风格 ——
``GenerateScript`` + ``SetProjectBrandProfile`` 绑定后，六维逐维注入 prompt，
禁用词零出现由 ``generate_script`` 硬校验兜底。

用法：

    python scripts/seed_creator_styles.py --db <stepwork.db 路径>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from worker.runtime.bootstrap import MIGRATIONS_DIR  # noqa: E402
from worker.runtime.commands.bus import dispatch  # noqa: E402
from worker.runtime.db.connection import connect  # noqa: E402
from worker.runtime.db.migrations import run_migrations  # noqa: E402
from worker.runtime.db.repos import Repos  # noqa: E402
from worker.runtime.deps import Deps  # noqa: E402

_PRESETS = _REPO / "scripts" / "creator_style_presets.json"
_WS = "ws-creator-styles"


async def _run(db_path: str) -> None:
    conn = connect(db_path)
    run_migrations(conn, MIGRATIONS_DIR)
    repos = Repos(conn)
    deps = Deps(repos=repos)
    deps.repos.workspaces.ensure(_WS)

    presets = json.loads(_PRESETS.read_text(encoding="utf-8"))
    for preset in presets:
        payload = {
            "name": f"Creator·{preset['name']}（{preset['domain']}）",
            "positioning": preset["positioning"],
            "audience": preset["audience"],
            "tone": preset["tone"],
            "styleDna": preset["styleDna"],
            "bannedExpressions": preset["bannedExpressions"],
        }
        out = await dispatch(
            {
                "commandId": f"seed-{preset['id']}",
                "commandType": "CreateBrandProfile",
                "schemaVersion": "1",
                "actor": {"type": "user", "id": "seed"},
                "source": "cli",
                "workspaceId": _WS,
                "payload": payload,
                "requestedAt": "2026-09-09T12:00:00+00:00",
            },
            deps,
        )
        if not out["ok"]:
            raise SystemExit(f"{preset['id']} 导入失败: {out.get('error')}")
        profile = out["detail"]["profile"]
        print(f"[ok] {profile['name']} -> {profile['id']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="目标 stepwork.db 路径")
    args = parser.parse_args()
    asyncio.run(_run(args.db))
