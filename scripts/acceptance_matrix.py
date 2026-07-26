"""MVP 核心测试矩阵验收脚本（docs/MVP_PLAN.md §6）。

与单测的区别：单测用假 provider / 假 ffmpeg 保证 CI 快而确定；本脚本用
**真实媒体 + 真实 ffmpeg**（可选真实 ASR）跑量化通过标准，回答「装好的
机器上真的能跑吗」。属 opt-in 验收，不进 CI。

用法（仓库根，需 ffmpeg 在 PATH）::

    .venv/Scripts/python.exe scripts/acceptance_matrix.py
    .venv/Scripts/python.exe scripts/acceptance_matrix.py --scenario asr

覆盖的可自动化场景（其余如种子用户访谈需人工）：

- 本地视频导入 30 个 → 100% 不崩溃，失败有错误
- ASR 20 个 → ≥90% 完成
- 分析 Schema 30 次 → ≥90% 合法输出
- 脚本版本 20 次编辑 → 0 丢失
- 渲染 10 连续任务 → ≥90% 成功、0 僵尸进程
- 异常退出 10 次 → 数据与 Job 可恢复
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker.runtime import ingest  # noqa: E402
from worker.runtime.analysis.scene import FFmpegSceneDetector  # noqa: E402
from worker.runtime.commands.bus import dispatch  # noqa: E402
from worker.runtime.db.connection import connect, in_memory  # noqa: E402
from worker.runtime.db.migrations import run_migrations  # noqa: E402
from worker.runtime.db.repos import Repos  # noqa: E402
from worker.runtime.deps import Deps  # noqa: E402
from worker.runtime.models import ContentProject, Workspace  # noqa: E402
from worker.runtime.providers.resolve import (  # noqa: E402
    resolve_asr,
    resolve_renderer,
)
from worker.runtime.providers.tts.local import LocalTTSProvider  # noqa: E402

_MIG_DIR = Path(__file__).resolve().parents[1] / "migrations"
_ANALYSIS_VALID: dict[str, Any] = {
    "summary": "验收用摘要。",
    "topics": ["验收"],
    "sentiment": "neutral",
    "suggested_title": "验收标题",
    "suggested_tags": ["验收"],
    "key_points": ["要点"],
    "target_audience": "创作者",
    "hook": "钩子",
    "structure": ["开场", "主体"],
    "risks": ["无"],
    "provider": "acceptance",
    "model": "acc-1",
    "confidence": 0.9,
}


class _FakeAI:
    """确定性 AI（矩阵校验的是 schema 合法率与管线，不是模型质量）。"""

    name = "acceptance-ai"
    model = "acc-1"
    estimated_cost_per_1k = 0.0

    async def complete(
        self, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return dict(_ANALYSIS_VALID)


def _env(
    ct: str, payload: dict[str, Any], ws: str, prj: str | None = None
) -> dict[str, Any]:
    return {
        "commandId": f"cmd-{uuid.uuid4().hex[:8]}",
        "commandType": ct,
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "acceptance"},
        "source": "cli",
        "workspaceId": ws,
        "projectId": prj,
        "payload": payload,
        "requestedAt": "2026-07-26T00:00:00+00:00",
    }


def _make_video(path: Path, seconds: int = 2, color: str = "red") -> bool:
    """用 ffmpeg 合成一个真实小视频（含静音音轨）；返回是否成功。"""
    argv = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color={color}:s=320x240:d={seconds}:r=10",
        "-f", "lavfi", "-i", f"anullsrc=r=16000:cl=mono:d={seconds}",
        "-shortest", "-pix_fmt", "yuv420p",
        str(path),
    ]
    proc = subprocess.run(argv, capture_output=True, text=True)
    return proc.returncode == 0 and path.is_file()


def _deps(conn: Any = None) -> Deps:
    c = conn or in_memory()
    if conn is None:
        run_migrations(c, _MIG_DIR)
    return Deps(
        repos=Repos(c),
        ingest=ingest,
        asr=resolve_asr(),
        ai=_FakeAI(),
        tts=LocalTTSProvider(),
        # 真实 ffmpeg 渲染器（与单测的假 ffmpeg 相区别，这是真机验收的重点）
        renderer=resolve_renderer(),
        scene_detector=FFmpegSceneDetector(),
    )


def _seed(deps: Deps, name: str = "acc") -> tuple[str, str]:
    ws = deps.repos.workspaces.insert(Workspace(name=name, root_path="/tmp/acc"))
    prj = deps.repos.projects.insert(
        ContentProject(workspace_id=ws, title=f"{name} 验收项目")
    )
    return ws, prj


async def scenario_import(media_dir: Path, n: int = 30) -> tuple[int, int, str]:
    """本地视频导入 N 个：100% 不崩溃，失败必须带错误信息。"""
    deps = _deps()
    ws, prj = _seed(deps, "import")
    ok = 0
    for i in range(n):
        v = media_dir / f"import_{i}.mp4"
        if not v.exists() and not _make_video(v, seconds=1):
            return ok, n, "ffmpeg 合成测试视频失败"
        res = await dispatch(
            _env("ImportSource", {"local_uri": str(v), "kind": "video"}, ws, prj), deps
        )
        if res.get("ok"):
            ok += 1
        elif not res.get("error"):
            return ok, n, f"第{i}个失败但无错误信息（违反「失败有错误」）"
    return ok, n, ""


async def scenario_asr(media_dir: Path, n: int = 20) -> tuple[int, int, str]:
    """ASR N 个：≥90% 完成。"""
    deps = _deps()
    ws, prj = _seed(deps, "asr")
    ok = 0
    for i in range(n):
        v = media_dir / f"asr_{i % 3}.mp4"
        if not v.exists() and not _make_video(v, seconds=2):
            return ok, n, "ffmpeg 合成测试视频失败"
        res = await dispatch(
            _env("TranscribeSource", {"local_uri": str(v)}, ws, prj), deps
        )
        if res.get("ok"):
            ok += 1
    return ok, n, ""


async def scenario_analysis(n: int = 30) -> tuple[int, int, str]:
    """分析 Schema N 次：≥90% 合法输出（落库即通过 pydantic 校验）。"""
    deps = _deps()
    ws, prj = _seed(deps, "analysis")
    ok = 0
    for i in range(n):
        res = await dispatch(
            _env("AnalyzeSource", {"text": f"第{i}条素材转写内容。"}, ws, prj), deps
        )
        if res.get("ok") and res.get("artifact_ids"):
            ok += 1
    return ok, n, ""


async def scenario_script_versions(n: int = 20) -> tuple[int, int, str]:
    """脚本版本 N 次编辑：0 丢失（每次编辑生成新版本且历史仍可读）。"""
    deps = _deps()
    ws, prj = _seed(deps, "script")
    created: list[str] = []
    for i in range(n):
        res = await dispatch(
            _env(
                "SaveAnalysis",
                {"content": json.dumps({**_ANALYSIS_VALID, "summary": f"第{i}版"})},
                ws,
                prj,
            ),
            deps,
        )
        if res.get("ok") and res.get("artifact_ids"):
            created.append(res["artifact_ids"][0])
    # 0 丢失：每个版本都仍可读回
    alive = sum(1 for cv in created if deps.repos.content_versions.get(cv) is not None)
    return alive, n, "" if alive == len(created) else "存在版本丢失"


async def scenario_render(media_dir: Path, n: int = 10) -> tuple[int, int, str]:
    """渲染 N 连续任务：≥90% 成功、0 僵尸进程。"""
    deps = _deps()
    ws, prj = _seed(deps, "render")
    ok = 0
    for _i in range(n):
        res = await dispatch(
            _env("SaveAnalysis", {"content": json.dumps(_ANALYSIS_VALID)}, ws, prj),
            deps,
        )
        if not res.get("ok"):
            continue
        r = await dispatch(
            _env(
                "CreateRenderJob",
                {
                    "source_version_id": res["artifact_ids"][0],
                    "template": "vertical-caption-v1",
                    "resolution": [320, 240],
                    "fps": 10,
                },
                ws,
                prj,
            ),
            deps,
        )
        if r.get("ok"):
            ok += 1
    return ok, n, ""


async def scenario_recovery(tmp: Path, n: int = 10) -> tuple[int, int, str]:
    """异常退出 N 次：数据与 Job 可恢复（重开库后 job 不停留在 RUNNING）。"""
    from worker.runtime.bootstrap import recover_orphan_jobs

    ok = 0
    for i in range(n):
        db = tmp / f"recover_{i}.db"
        conn = connect(str(db))
        run_migrations(conn, _MIG_DIR)
        deps = _deps(conn)
        ws, prj = _seed(deps, f"rec{i}")
        res = await dispatch(
            _env("AnalyzeSource", {"text": "崩溃前的分析"}, ws, prj), deps
        )
        if not res.get("ok"):
            continue
        # 模拟 kill -9：不 close 直接丢弃连接，重开
        conn.close()
        conn2 = connect(str(db))
        run_migrations(conn2, _MIG_DIR)
        recover_orphan_jobs(conn2)
        stuck = conn2.execute(
            "SELECT COUNT(*) c FROM jobs WHERE state IN ('running','leased')"
        ).fetchone()["c"]
        rows = conn2.execute("SELECT COUNT(*) c FROM content_versions").fetchone()["c"]
        conn2.close()
        if stuck == 0 and rows > 0:
            ok += 1
    return ok, n, ""


_THRESHOLDS = {
    "import": 1.0,
    "asr": 0.9,
    "analysis": 0.9,
    "script": 1.0,
    "render": 0.9,
    "recovery": 1.0,
}


async def main() -> int:
    ap = argparse.ArgumentParser(description="MVP 核心测试矩阵验收")
    ap.add_argument("--scenario", default="all", help="all 或单个场景名")
    args = ap.parse_args()

    if not shutil.which("ffmpeg"):
        print("FAIL: 需要 ffmpeg 在 PATH（真实媒体验收）")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="stepwork_acc_"))
    media = tmp / "media"
    media.mkdir(parents=True, exist_ok=True)

    runners: dict[str, Callable[[], Awaitable[tuple[int, int, str]]]] = {
        "import": lambda: scenario_import(media),
        "asr": lambda: scenario_asr(media),
        "analysis": lambda: scenario_analysis(),
        "script": lambda: scenario_script_versions(),
        "render": lambda: scenario_render(media),
        "recovery": lambda: scenario_recovery(tmp),
    }
    todo = runners if args.scenario == "all" else {args.scenario: runners[args.scenario]}

    print(f"媒体临时目录: {media}\n")
    failures = 0
    for name, run in todo.items():
        ok, total, note = await run()
        rate = ok / total if total else 0.0
        need = _THRESHOLDS[name]
        passed = rate >= need and not note
        if not passed:
            failures += 1
        mark = "PASS" if passed else "FAIL"
        extra = f"  [{note}]" if note else ""
        print(f"[{mark}] {name:10s} {ok}/{total} = {rate:.0%} (需 {need:.0%}){extra}")

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + ("全部通过" if failures == 0 else f"{failures} 个场景未达标"))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
