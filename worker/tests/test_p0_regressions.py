"""三维 Review 后的 P0 行为回归测试。

对应 yt-dev-review 阶段 3 的 5 个 P0：

1. ``Repos.rebind`` 必须重绑 **全部** 子 repo（历史 bug 是漏 ``video_scenes``
   / ``hotspots``，Restore 后 S2/S5 挂到重启）。
2. 日志掩码覆盖 ``passphrase`` / ``credential`` / 裸 ``key``（与
   ``config._SECRET_RE`` 字段清单同步），且 JSON 形态掩码后仍是合法 JSON
   （历史 bug 是把整段 ``"apiKey": "sk-…"`` 换成 ``apiKey=••••``，引号被吞）。
3. ``JobRepo.update_state`` 终态迁移也要带 ``state NOT IN (terminal)`` 守卫
   （历史 bug 是 ``CANCELLED → SUCCEEDED`` 无阻挡，ffmpeg 线程完成时会把
   用户取消的 job 复活并挂上产物）。
4. ``probe_duration`` 两处 ``subprocess.run`` 必须带 timeout（历史 bug 是无
   超时 → ffprobe 挂起即冻结整个事件循环）。
5. ``get_provider_bundle`` 缓存 + ``apply_override`` 后失效（历史 bug 是每条
   命令重建 Provider，Whisper 模型反复重载、``shutil.which`` 反复扫盘）。

每个测试都用最小可复现的**修复前**症状命名，方便未来 grep 定位。
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from worker.runtime import logging_config
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.logging_config import mask_secrets
from worker.runtime.models import Job, JobStage, JobState
from worker.runtime.providers import resolve
from worker.runtime.render import ffmpeg_runner

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


# ---------------------------------------------------------------------------
# P0-1  Repos.rebind
# ---------------------------------------------------------------------------


def test_repos_rebind_reaches_every_subrepo() -> None:
    """重绑必须覆盖 ``__init__`` 里的所有子 repo —— 曾经漏过 ``video_scenes``
    与 ``hotspots``，S2/S5 一路抛 ProgrammingError。"""
    conn_a = in_memory()
    run_migrations(conn_a, _MIG_DIR)
    repos = Repos(conn_a)

    # 新建一个不同身份的内存连接（``:memory:`` 每次都是独立 DB），
    # 让"旧连接关闭、新连接顶上"的语义在测试里能真实验证。
    # 走 :func:`in_memory` 保证 row_factory / 外键跟生产连接一致。
    conn_b = in_memory()
    run_migrations(conn_b, _MIG_DIR)

    repos.rebind(conn_b)

    assert repos.conn is conn_b
    # 显式列出所有子 repo，任何新增 repo 若没走 rebind 会在此暴露
    for name in (
        "workspaces",
        "projects",
        "source_assets",
        "jobs",
        "content_versions",
        "video_scenes",
        "hotspots",
    ):
        sub = getattr(repos, name)
        assert sub.conn is conn_b, f"{name} 未随 rebind 一起重绑"


def test_repos_rebind_survives_old_conn_close() -> None:
    """端到端复现备份恢复的旧 bug：close 老 conn 后 rebind，任何子 repo 操作
    必须走新连接、不再抛 ``sqlite3.ProgrammingError``。"""
    conn_a = in_memory()
    run_migrations(conn_a, _MIG_DIR)
    repos = Repos(conn_a)
    # 先用旧连接做一次插入建立"旧连接确实在用"的印象
    ws = repos.workspaces.ensure("ws-pre-rebind")
    assert ws.id is not None

    conn_b = in_memory()
    run_migrations(conn_b, _MIG_DIR)
    conn_a.close()
    repos.rebind(conn_b)

    # 关键：走历史上被漏掉的两个子 repo 各一次真实读写
    repos.workspaces.ensure("ws-post-rebind")  # 触发 workspaces.conn
    # video_scenes.list_by_version 只 SELECT，空表也返回 [] 而非抛错
    assert repos.video_scenes.list_by_version("no-such-version") == []
    # hotspots.list_recent 同理
    assert repos.hotspots.list_recent(workspace_id="ws-post-rebind") == []


# ---------------------------------------------------------------------------
# P0-2  日志掩码：字段清单 + JSON 结构
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("apiKey", "sk-live-A"),
        ("passphrase", "PP-SECRET-B"),
        ("credential", "CR-SECRET-C"),
        ("key", "BARE-KEY-D"),  # 裸 key 曾在 _SECRET_PATTERN 完全漏网
        ("accessKey", "AK-SECRET-E"),
        ("secret", "S-SECRET-F"),
        ("token", "T-SECRET-G"),
        ("password", "P-SECRET-H"),
    ],
)
def test_mask_secret_json_preserves_field_and_validity(
    field: str, value: str
) -> None:
    """JSON 形态的密钥掩码后：值被吃掉、字段名保留、整行仍是合法 JSON。

    修复前：``apiKey": "sk-live-A"`` 整段换成 ``apiKey=••••``，开头的引号
    保留、结尾引号被吞 —— 结果 ``{"apiKey=••••}`` 无法 json.loads。
    修复后：``{"apiKey": "••••"}`` 保持 JSON 结构。
    """
    payload = {field: value, "other": 1}
    raw = json.dumps(payload)
    masked = mask_secrets(raw)

    assert value not in masked, f"{field} 明文仍泄漏：{masked}"
    # JSON 结构必须保留 —— 这是 W9 归档里的 P1（结构化日志不可 grep）
    parsed: dict[str, Any] = json.loads(masked)
    assert field in parsed, f"字段名丢失：{masked}"
    assert parsed["other"] == 1, "非敏感字段应原样保留"


def test_mask_preserves_separator_style() -> None:
    """``:`` 与 ``=`` 两种分隔符各自回填 —— 修前统一换成 ``=`` 会让 bare 与
    JSON 混排的调试输出更丑。"""
    assert mask_secrets("apiKey=abc123") == "apiKey=••••"
    masked_json = mask_secrets('{"apiKey": "abc123"}')
    parsed = json.loads(masked_json)
    assert parsed == {"apiKey": "••••"}


def test_mask_does_not_hit_benign_words() -> None:
    """左边界 ``(?<![\\w-])`` 拦住 ``monkey=…`` / ``keyboard=…`` 等假阳性。"""
    for benign in (
        "monkey=1",
        "keyboard=1",
        "donkey: 3",
        "the token bucket algorithm",
    ):
        assert mask_secrets(benign) == benign, f"普通词被误伤：{benign}"


def test_mask_is_idempotent() -> None:
    """对已掩码串再跑一次结果不变（诊断包多次收集时不能把 ``••••`` 变成乱码）。"""
    once = mask_secrets('{"apiKey": "sk-live-secret"}')
    twice = mask_secrets(once)
    assert once == twice


def test_masking_formatter_output_is_parseable_json() -> None:
    """整行 JSON 日志经过 MaskingFormatter 后必须仍可 ``json.loads``
    —— 修前每条含 dict payload 的行都是非法 JSON。"""
    import logging

    fmt = logging_config.MaskingFormatter(logging_config._JSON_LINE_FMT)
    record = logging.LogRecord(
        name="worker.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='UpdateConfig payload summary: {"apiKey": "sk-live-Z", "tts": {"passphrase": "PP-Z"}}',
        args=(),
        exc_info=None,
    )
    line = fmt.format(record)
    parsed = json.loads(line)
    assert "sk-live-Z" not in parsed["msg"]
    assert "PP-Z" not in parsed["msg"]


# ---------------------------------------------------------------------------
# P0-3  Job 终态复活守卫
# ---------------------------------------------------------------------------


@pytest.fixture
def repos_with_jobs() -> Iterator[Repos]:
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    yield Repos(conn)


def test_terminal_to_terminal_transition_is_rejected(repos_with_jobs: Repos) -> None:
    """CANCELLED → SUCCEEDED 必须被拒绝 —— 修前用户取消后 ffmpeg 线程完成
    会把 job 复活并挂上产物（真实竞态，非假想）。"""
    repos = repos_with_jobs
    repos.workspaces.ensure("ws-race")
    job = Job(
        job_type="render",
        payload={},
        state=JobState.RUNNING,
        stage=JobStage.RENDERING,
    )
    repos.jobs.create(job)

    cancelled = repos.jobs.update_state(job.id, JobState.CANCELLED)
    assert cancelled.state is JobState.CANCELLED

    # 关键断言：worker 线程的完成信号不能把已取消的 job 复活
    resurrected = repos.jobs.update_state(
        job.id, JobState.SUCCEEDED, progress=1.0
    )
    assert resurrected.state is JobState.CANCELLED, (
        "CANCELLED 被 SUCCEEDED 覆盖 —— 用户取消后 worker 会挂上假产物，"
        "PRD-CANCEL 语义被破坏"
    )
    # progress 不应被回写到 1.0
    assert resurrected.progress != 1.0


def test_terminal_states_are_absorbing(repos_with_jobs: Repos) -> None:
    """FAILED → EXPIRED、SUCCEEDED → FAILED 等所有终态互转都应被拒。"""
    repos = repos_with_jobs
    repos.workspaces.ensure("ws-absorb")
    for initial in (
        JobState.SUCCEEDED,
        JobState.FAILED,
        JobState.CANCELLED,
        JobState.EXPIRED,
    ):
        for target in (
            JobState.SUCCEEDED,
            JobState.FAILED,
            JobState.CANCELLED,
            JobState.EXPIRED,
        ):
            job = Job(job_type="x", payload={}, state=initial)
            repos.jobs.create(job)
            # 直接把创建后的 state 强制写入（create 默认 PENDING），
            # 以便精确构造"已经是 initial 终态"的前置条件。
            repos.conn.execute(
                "UPDATE jobs SET state=? WHERE id=?", (initial.value, job.id)
            )
            repos.conn.commit()

            result = repos.jobs.update_state(job.id, target)
            if initial is target:
                # 同态重写：目标状态就是当前状态，SQL 层拒绝但读回仍是它
                assert result.state is initial
            else:
                assert result.state is initial, (
                    f"{initial.value} → {target.value} 不该成功"
                )


def test_non_terminal_still_guards_against_regression(
    repos_with_jobs: Repos,
) -> None:
    """W3 就有的 RUNNING 不能覆盖终态 —— 新加守卫不能反向打破既有语义。"""
    repos = repos_with_jobs
    repos.workspaces.ensure("ws-runnig-guard")
    job = Job(job_type="x", payload={}, state=JobState.CANCELLED)
    repos.jobs.create(job)
    repos.conn.execute(
        "UPDATE jobs SET state=? WHERE id=?", (JobState.CANCELLED.value, job.id)
    )
    repos.conn.commit()

    after = repos.jobs.update_state(job.id, JobState.RUNNING, progress=0.5)
    assert after.state is JobState.CANCELLED


# ---------------------------------------------------------------------------
# P0-4  probe_duration 有超时
# ---------------------------------------------------------------------------


def test_probe_duration_passes_timeout_to_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ffprobe 挂起 = 冻结事件循环（心跳停 / CancelJob 排队）。subprocess.run
    必须带 timeout。用 monkeypatch 观察 kwargs。"""
    captured: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return subprocess.CompletedProcess(
            args=kwargs.get("args", []), returncode=1, stdout="", stderr=""
        )

    monkeypatch.setattr("worker.runtime.render.ffmpeg_runner.subprocess.run", fake_run)
    # ``_ffprobe_candidates`` 用 ``os.path.isfile`` 过滤假路径，会让 fake_run
    # 一次都跑不到 —— 这里放行 isfile，才能真实观察 kwargs。
    monkeypatch.setattr("worker.runtime.render.ffmpeg_runner.os.path.isfile", lambda _: True)

    # 具体抛 FFmpegFailed 还是 FFmpegUnavailable 取决于兜底分支，本用例只关心
    # 「真跑到 subprocess.run 且带了 timeout」这一条契约。
    with pytest.raises(Exception):  # noqa: B017 - 断言只针对 kwargs
        ffmpeg_runner.probe_duration("fake.mp4", ffprobe_bin="fake-ffprobe")

    assert captured.get("timeout"), (
        f"probe_duration 未向 subprocess.run 传 timeout：{captured}"
    )


def test_scene_detector_subprocess_has_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """场景检测的 subprocess.run 也必须带 timeout —— 外层 to_thread 只保证
    事件循环不卡，挂死的 ffmpeg 会永久占用 default executor 里的一个线程。"""
    from worker.runtime.analysis import scene as scene_mod

    captured: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return subprocess.CompletedProcess(
            args=kwargs.get("args", []), returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr("worker.runtime.analysis.scene.subprocess.run", fake_run)
    det = scene_mod.FFmpegSceneDetector()
    det.bin_path = "ffmpeg"  # 强制 available，绕过 shutil.which 判定
    det._detect_sync("fake.mp4", 0.4)

    assert captured.get("timeout"), (
        f"scene detector 未向 subprocess.run 传 timeout：{captured}"
    )


# ---------------------------------------------------------------------------
# P0-5  Provider bundle 缓存
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_bundle_cache() -> Iterator[None]:
    """每测试前后清空缓存，避免用例之间互相污染。"""
    with resolve._PROVIDER_BUNDLE_LOCK:
        resolve._PROVIDER_BUNDLE_CACHE.clear()
    yield
    with resolve._PROVIDER_BUNDLE_LOCK:
        resolve._PROVIDER_BUNDLE_CACHE.clear()


def test_provider_bundle_is_cached_per_workspace(
    clean_bundle_cache: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一 workspace 二次取 bundle 必须复用同一实例 —— 修前 Whisper 每请求
    重建，模型重载数秒起。"""
    calls = {"n": 0}
    sentinel = object()

    def fake_asr(_ws: str | None) -> Any:
        calls["n"] += 1
        return sentinel

    monkeypatch.setattr(resolve, "resolve_asr", fake_asr)
    monkeypatch.setattr(resolve, "resolve_ai", lambda _ws: None)
    monkeypatch.setattr(resolve, "resolve_tts", lambda _ws: None)
    monkeypatch.setattr(resolve, "resolve_image", lambda _ws: None)
    monkeypatch.setattr(resolve, "resolve_renderer", lambda: None)
    monkeypatch.setattr(resolve, "resolve_scene_detector", lambda: None)

    first = resolve.get_provider_bundle("ws-A")
    second = resolve.get_provider_bundle("ws-A")

    assert calls["n"] == 1, f"resolve_asr 被调用 {calls['n']} 次，缓存未生效"
    assert first["asr"] is second["asr"] is sentinel


def test_provider_bundle_scoped_by_workspace(
    clean_bundle_cache: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不同 workspace 各建各的 —— 密钥覆盖层按 workspace_id 隔离。"""
    seen: list[str | None] = []

    def fake_asr(ws: str | None) -> Any:
        seen.append(ws)
        return object()

    monkeypatch.setattr(resolve, "resolve_asr", fake_asr)
    monkeypatch.setattr(resolve, "resolve_ai", lambda _ws: None)
    monkeypatch.setattr(resolve, "resolve_tts", lambda _ws: None)
    monkeypatch.setattr(resolve, "resolve_image", lambda _ws: None)
    monkeypatch.setattr(resolve, "resolve_renderer", lambda: None)
    monkeypatch.setattr(resolve, "resolve_scene_detector", lambda: None)

    resolve.get_provider_bundle("ws-A")
    resolve.get_provider_bundle("ws-B")
    assert seen == ["ws-A", "ws-B"]


def test_apply_override_invalidates_bundle_cache(
    clean_bundle_cache: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """保存设置后 Provider 必须重建 —— 否则 apiKey 换了还打老 endpoint。"""
    calls = {"n": 0}

    def fake_asr(_ws: str | None) -> Any:
        calls["n"] += 1
        return object()

    monkeypatch.setattr(resolve, "resolve_asr", fake_asr)
    monkeypatch.setattr(resolve, "resolve_ai", lambda _ws: None)
    monkeypatch.setattr(resolve, "resolve_tts", lambda _ws: None)
    monkeypatch.setattr(resolve, "resolve_image", lambda _ws: None)
    monkeypatch.setattr(resolve, "resolve_renderer", lambda: None)
    monkeypatch.setattr(resolve, "resolve_scene_detector", lambda: None)

    resolve.get_provider_bundle("ws-X")
    resolve.get_provider_bundle("ws-X")
    assert calls["n"] == 1

    resolve.apply_override("ws-X", {"llm": {"apiKey": "sk-new"}})
    resolve.get_provider_bundle("ws-X")
    assert calls["n"] == 2, "apply_override 未作废 Provider 缓存"
