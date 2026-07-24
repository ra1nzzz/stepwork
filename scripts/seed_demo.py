"""W9 L.43 种子数据脚本：注入 5 个示例项目（idempotent）。

每个 project 含完整链路：ImportSource → TranscribeSource → AnalyzeSource →
GenerateTopic → GenerateScript，共 1 source + 1 transcript + 1 analysis +
1 topic_proposal + 1 script。

设计决策（W9_PLAN D6）：走 ``dispatch``（命令总线）而非直写 SQL，
保证 schema 一致性 + 可重复执行（按 ``title`` 去重）。

AI 内容由 ``_SeedAI`` 确定性生成（fixture），不依赖真实 AI provider；
ASR/TTS 走默认 ``local`` provider（离线确定性）。数据落到
``$STEPWORK_HOME/stepwork.db``。

用法::

    # 默认：写入 $STEPWORK_HOME/stepwork.db
    python scripts/seed_demo.py

    # 显式指定 DB 路径（如测试）
    python scripts/seed_demo.py --db-path /tmp/seed.db

    # 列出现有种子项目（不写入）
    python scripts/seed_demo.py --list
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# 确保仓库根目录在 sys.path（直接 ``python scripts/seed_demo.py`` 时）
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from worker.runtime import ingest  # noqa: E402
from worker.runtime.bootstrap import bootstrap_db  # noqa: E402
from worker.runtime.commands.bus import dispatch  # noqa: E402
from worker.runtime.db.connection import connect  # noqa: E402
from worker.runtime.db.migrations import run_migrations  # noqa: E402
from worker.runtime.db.repos import Repos  # noqa: E402
from worker.runtime.deps import Deps  # noqa: E402
from worker.runtime.models import ContentProject  # noqa: E402
from worker.runtime.providers.asr.local import LocalASRProvider  # noqa: E402
from worker.runtime.providers.tts.local import LocalTTSProvider  # noqa: E402
from worker.runtime.state import WorkerState  # noqa: E402

_MIGRATIONS_DIR = _REPO_ROOT / "migrations"

# 5 个示例项目（title 即去重 key）
_SEED_PROJECTS: list[dict[str, Any]] = [
    {
        "title": "短视频入门：3 个反常识开场",
        "source_uri": "file:///tmp/seed/short-intro.mp4",
        "source_hash": "seed-hash-short-intro",
        "analysis_summary": "拆解 3 个反常识开场钩子，适用于知识/科普类短视频。",
        "topic_title": "反常识开场：让前 3 秒抓住眼球",
        "topic_rationale": "反常识打破预期，停留率提升显著",
        "topic_hook": "你以为对的，其实是错的",
        "script_title": "反常识开场：让前 3 秒抓住眼球",
        "script_body": (
            "（0-3s）钩子：你以为对的，其实是错的\n"
            "（3-10s）反常识 1：先抛结论\n"
            "（10-20s）反常识 2：举例反转\n"
            "（20-30s）收尾：关注我看更多"
        ),
    },
    {
        "title": "电商带货：产品卖点拆解",
        "source_uri": "file:///tmp/seed/ecom-product.mp4",
        "source_hash": "seed-hash-ecom-product",
        "analysis_summary": "从使用场景、痛点、差异化三个维度拆解产品卖点。",
        "topic_title": "产品卖点三段式：场景-痛点-差异",
        "topic_rationale": "结构化卖点便于主播快速记忆和发挥",
        "topic_hook": "这个产品解决了什么问题？",
        "script_title": "产品卖点三段式：场景-痛点-差异",
        "script_body": (
            "（0-3s）钩子：这个产品解决了什么问题？\n"
            "（3-15s）场景：日常使用场景带入\n"
            "（15-30s）痛点：没有它会怎样\n"
            "（30-45s）差异：对比竞品优势\n"
            "（45-60s）收尾：点击购买"
        ),
    },
    {
        "title": "知识科普：把复杂说简单",
        "source_uri": "file:///tmp/seed/sci-explain.mp4",
        "source_hash": "seed-hash-sci-explain",
        "analysis_summary": "用生活类比解释专业概念，降低理解门槛。",
        "topic_title": "类比法科普：用生活概念解释专业术语",
        "topic_rationale": "类比法是科普类内容最有效的降低门槛手段",
        "topic_hook": "这个专业术语其实你每天都在用",
        "script_title": "类比法科普：用生活概念解释专业术语",
        "script_body": (
            "（0-3s）钩子：这个专业术语其实你每天都在用\n"
            "（3-15s）术语：引入要解释的概念\n"
            "（15-30s）类比：用生活场景映射\n"
            "（30-45s）深化：类比外的关键差异\n"
            "（45-60s）收尾：点赞收藏"
        ),
    },
    {
        "title": "Vlog 日常：一周复盘",
        "source_uri": "file:///tmp/seed/vlog-weekly.mp4",
        "source_hash": "seed-hash-vlog-weekly",
        "analysis_summary": "一周亮点片段合集 + 反思总结，适合周更节奏。",
        "topic_title": "周复盘：3 个亮点 + 1 个反思",
        "topic_rationale": "复盘结构化让 vlog 不只是流水账",
        "topic_hook": "这周最值得记的 3 件事",
        "script_title": "周复盘：3 个亮点 + 1 个反思",
        "script_body": (
            "（0-3s）钩子：这周最值得记的 3 件事\n"
            "（3-15s）亮点 1：周一的小突破\n"
            "（15-30s）亮点 2：周三的意外收获\n"
            "（30-45s）亮点 3：周五的放松时刻\n"
            "（45-60s）反思：下周想改进什么\n"
            "（60-75s）收尾：关注我下周见"
        ),
    },
    {
        "title": "教程类：剪映快速上手",
        "source_uri": "file:///tmp/seed/tutorial-jianying.mp4",
        "source_hash": "seed-hash-tutorial-jianying",
        "analysis_summary": "剪映核心功能 5 分钟速览：导入-剪辑-字幕-特效-导出。",
        "topic_title": "5 分钟学会剪映核心流程",
        "topic_rationale": "教程类内容按流程拆步骤最清晰",
        "topic_hook": "5 分钟学会剪映核心流程",
        "script_title": "5 分钟学会剪映核心流程",
        "script_body": (
            "（0-3s）钩子：5 分钟学会剪映核心流程\n"
            "（3-15s）步骤 1：导入素材\n"
            "（15-30s）步骤 2：粗剪去废片\n"
            "（30-45s）步骤 3：加字幕\n"
            "（45-60s）步骤 4：加转场/特效\n"
            "（60-75s）步骤 5：导出设置\n"
            "（75-90s）收尾：关注我看进阶"
        ),
    },
]


class _SeedAI:
    """确定性 AI Provider：按 schema 形状返回对应 fixture 内容。

    与 test_e2e 的 _FakeAI 同构，但内容按种子项目主题定制，
    使每个 demo project 有差异化的分析/选题/脚本。
    """

    name = "seed-ai"
    model = "seed-1"
    estimated_cost_per_1k = 0.0

    def __init__(self, project_meta: dict[str, Any]) -> None:
        self._meta = project_meta

    async def complete(
        self, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if schema and schema.get("title") == "AnalysisReport":
            return {
                "summary": self._meta["analysis_summary"],
                "topics": [self._meta["topic_title"]],
                "sentiment": "positive",
                "suggested_title": self._meta["topic_title"],
                "suggested_tags": ["种子数据"],
                "key_points": [self._meta["topic_hook"]],
                "target_audience": "创作者",
                "provider": self.name,
                "model": self.model,
                "confidence": 0.85,
            }
        props = (schema or {}).get("properties", {}) if schema else {}
        if "angles" in props:
            return {
                "angles": [
                    {
                        "id": "a1",
                        "title": self._meta["topic_title"],
                        "rationale": self._meta["topic_rationale"],
                        "hook": self._meta["topic_hook"],
                    }
                ],
                # GenerateScript 复用同一 fake，顺带返回 title/body
                "title": self._meta["script_title"],
                "body": self._meta["script_body"],
            }
        return {
            "title": self._meta["script_title"],
            "body": self._meta["script_body"],
        }


def _build_deps(db_path: str | None, project_meta: dict[str, Any]) -> Deps:
    """构造种子注入所需 Deps（真实文件 DB + 种子 AI + local ASR/TTS）。"""
    state = WorkerState()
    if db_path:
        # 显式路径：直接 connect + migrate（不走 bootstrap 的自动备份逻辑，
        # 避免种子脚本意外触发生产库备份）
        conn = connect(db_path)
        run_migrations(conn, _MIGRATIONS_DIR)
        state.db_conn = conn
        state.db_path = db_path
    else:
        # 默认路径：走 bootstrap（含 $STEPWORK_HOME 解析 + 迁移）
        bootstrap_db(state)

    return Deps(
        repos=Repos(state.db_conn),
        ingest=ingest,
        asr=LocalASRProvider(),
        ai=_SeedAI(project_meta),
        tts=LocalTTSProvider(),
        # renderer 不需要（种子不跑 CreateRenderJob）
        renderer=None,
    )


def _envelope(
    command_type: str,
    payload: dict[str, Any],
    *,
    workspace_id: str,
    project_id: str | None = None,
) -> dict[str, Any]:
    """构造符合 command-envelope.schema.json 的信封 dict。"""
    return {
        "commandId": f"seed-{command_type}-{uuid.uuid4().hex[:8]}",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "seed-script"},
        "source": "cli",
        "workspaceId": workspace_id,
        "projectId": project_id,
        "payload": payload,
        "requestedAt": datetime.now(UTC).isoformat(),
    }


def _project_title_exists(conn: Any, title: str) -> bool:
    """检查 ``content_projects`` 表是否已有同 title 的项目（去重 key）。"""
    row = conn.execute(
        "SELECT id FROM content_projects WHERE title=?", (title,)
    ).fetchone()
    return row is not None


async def _seed_one(
    project_meta: dict[str, Any], db_path: str | None
) -> dict[str, Any]:
    """注入一个种子项目，返回结果摘要 dict。

    若同名项目已存在则跳过（idempotent），返回 ``{"skipped": True}``。
    """
    # 每个 project 独立 Deps（因为 _SeedAI 持有 project_meta）
    deps = _build_deps(db_path, project_meta)
    conn = deps.repos.conn

    title = project_meta["title"]
    if _project_title_exists(conn, title):
        return {"title": title, "skipped": True}

    # 复用/创建 workspace（idempotent）
    ws = deps.repos.workspaces.get_or_create(
        name="seed-demo", root_path="STEPWORK_HOME/seed"
    )
    ws_id = ws.id

    # 建 project
    prj = ContentProject(workspace_id=ws_id, title=title)
    prj_id = deps.repos.projects.insert(prj)

    steps: list[tuple[str, str, dict[str, Any]]] = [
        (
            "ImportSource",
            "导入素材",
            {
                "local_uri": project_meta["source_uri"],
                "content_hash": project_meta["source_hash"],
                "kind": "video",
            },
        ),
        (
            "TranscribeSource",
            "转写",
            {"opts": {"duration_sec": 12}},
        ),
        (
            "AnalyzeSource",
            "AI 分析",
            {},  # transcript_version_id 在运行时填入
        ),
        (
            "GenerateTopic",
            "生成选题",
            {"count": 1},
        ),
        (
            "GenerateScript",
            "生成脚本",
            {"topic_id": "a1"},
        ),
    ]

    transcript_id: str | None = None
    proposal_id: str | None = None
    artifact_chain: list[str] = []

    for cmd_type, label, base_payload in steps:
        payload = dict(base_payload)
        # 串接 artifact 链
        if cmd_type == "TranscribeSource":
            payload["asset_id"] = artifact_chain[-1]
        elif cmd_type == "AnalyzeSource":
            payload["transcript_version_id"] = transcript_id
        elif cmd_type == "GenerateTopic":
            payload["source_version_id"] = transcript_id
        elif cmd_type == "GenerateScript":
            payload["proposal_version_id"] = proposal_id

        res = await dispatch(
            _envelope(
                cmd_type,
                payload,
                workspace_id=ws_id,
                project_id=prj_id,
            ),
            deps,
        )
        if not res.get("ok"):
            return {
                "title": title,
                "error": f"{label} 失败: {res.get('error', res)}",
                "step": cmd_type,
            }

        # 记录 artifact 链
        if res.get("artifact_ids"):
            artifact_chain.extend(res["artifact_ids"])
            if cmd_type == "TranscribeSource":
                transcript_id = res["artifact_ids"][0]
            elif cmd_type == "GenerateTopic":
                proposal_id = res["artifact_ids"][0]

    return {
        "title": title,
        "project_id": prj_id,
        "artifact_chain": artifact_chain,
        "versions_created": len(artifact_chain),
    }


async def _run_seed(db_path: str | None) -> list[dict[str, Any]]:
    """注入全部 5 个种子项目，返回结果列表。"""
    results: list[dict[str, Any]] = []
    for i, meta in enumerate(_SEED_PROJECTS, 1):
        print(f"[{i}/{len(_SEED_PROJECTS)}] 注入: {meta['title']} ...", flush=True)
        res = await _seed_one(meta, db_path)
        if res.get("skipped"):
            print("  ✓ 跳过（已存在）", flush=True)
        elif res.get("error"):
            print(f"  ✗ {res['error']}", flush=True)
        else:
            print(
                f"  ✓ 完成: {res['versions_created']} versions, "
                f"chain={res['artifact_chain'][:3]}...",
                flush=True,
            )
        results.append(res)
    return results


def _list_seed_projects(db_path: str | None) -> int:
    """列出现有种子项目（不写入），返回匹配数量。"""
    deps = _build_deps(db_path, _SEED_PROJECTS[0])
    conn = deps.repos.conn
    seed_titles = [p["title"] for p in _SEED_PROJECTS]
    placeholders = ",".join("?" * len(seed_titles))
    rows = conn.execute(
        f"SELECT title, created_at FROM content_projects "
        f"WHERE title IN ({placeholders}) ORDER BY created_at",
        seed_titles,
    ).fetchall()
    print(f"种子项目（{len(rows)}/{len(_SEED_PROJECTS)} 已存在）:")
    for r in rows:
        print(f"  - {r['title']}  (created: {r['created_at']})")
    return len(rows)


def main() -> int:
    """CLI 入口：解析参数 → 注入种子数据 → 打印摘要。"""
    parser = argparse.ArgumentParser(
        description="注入 5 个示例项目（idempotent，按 title 去重）"
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="显式数据库路径；缺省走 $STEPWORK_HOME/stepwork.db",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="仅列出现有种子项目，不写入",
    )
    args = parser.parse_args()

    if args.list:
        _list_seed_projects(args.db_path)
        return 0

    print(
        f"开始注入种子数据 (db_path={args.db_path or '$STEPWORK_HOME/stepwork.db'})...",
        flush=True,
    )
    results = asyncio.run(_run_seed(args.db_path))

    # 摘要
    created = sum(1 for r in results if not r.get("skipped") and not r.get("error"))
    skipped = sum(1 for r in results if r.get("skipped"))
    failed = sum(1 for r in results if r.get("error"))
    print(
        f"\n摘要: {created} 创建 / {skipped} 跳过 / {failed} 失败 "
        f"(共 {len(results)})",
        flush=True,
    )

    if failed:
        for r in results:
            if r.get("error"):
                print(f"  失败: {r['title']} - {r['error']}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
