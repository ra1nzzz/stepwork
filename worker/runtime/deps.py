"""命令处理依赖注入容器（W3-W4 Batch 0）。

主代理统一构造并注入；各 handler 只声明所需字段，互不耦合。

**为什么给每个字段都上 Protocol**（dimension C 归档里的 P1）

此前 ``Deps`` 里 11 个字段有 9 个是 ``Any``：Provider 明明各自有 PEP 544
``Protocol``（ASR/AI/TTS/Image/Renderer/Publish），注入边界却退化成
``Any`` —— 一旦有人改了 Provider 的方法签名（比如 ``AIProvider.complete``
从 ``(prompt, schema)`` 改成 ``(**kwargs)``），mypy strict 一声不吭，
只有运行时 AttributeError 才被发现。

现在按 Protocol 标注：签名漂移会被 mypy 立刻抓到；``None`` 通过
``X | None`` 显式表达"该 provider 未配置 → handler 转译为 UNAVAILABLE"
的既有语义。``worker_state`` 用 ``TYPE_CHECKING`` 前向引用避免循环。

**为什么装配收口到 :func:`build_deps`**（dimension C 归档里的 P1）

此前有两条独立的 ``Deps`` 装配路径且**已经漂移**：

- :func:`worker.runtime.app.run_command`（CLI / MCP 门面）逐字段
  ``resolve_*`` —— 绕开了 :func:`providers.resolve.get_provider_bundle`
  的进程级缓存，每条命令都重建 Provider；同时 ``notify`` / ``worker_state``
  都没注入 → 9 个 handler 的 ``deps.notify`` 静默 no-op、
  ``RestoreWorkspace`` 的 ``worker_state.db_conn`` 回写被 ``if is not None``
  悄悄跳过。
- :func:`worker.runtime.handlers.commands.handle_command`（Rust sidecar）
  走缓存 + 注 notify / worker_state。

同一份"命令的依赖注入"在两个入口长得不一样，就是漂移的开始。
:func:`build_deps` 收口之后，两条路径都走它，Provider 缓存 / notify /
worker_state 一次装配、一处演进。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from worker.runtime import ingest as _ingest_module
from worker.runtime.analysis.scene import SceneDetector
from worker.runtime.db.repos import Repos
from worker.runtime.providers.ai.base import AIProvider
from worker.runtime.providers.asr.base import ASRProvider
from worker.runtime.providers.image.base import ImageProvider
from worker.runtime.providers.publish.base import PublishProvider
from worker.runtime.providers.renderer.base import RendererProvider
from worker.runtime.providers.tts.base import TTSProvider

if TYPE_CHECKING:
    from worker.runtime.state import WorkerState


@dataclass
class Deps:
    """Command Bus 注入依赖。

    Attributes:
        repos: 聚合 repo，与 worker 生命周期共享同一条 SQLite 连接。
        ingest: ``worker.runtime.ingest`` 模块引用（模块当对象传，方便测试
            直接 monkeypatch）。
        asr / ai / tts / image / renderer / scene_detector / publish:
            各领域 Provider；``None`` 表示未配置，handler 转译为
            ``UNAVAILABLE``（既有一贯做法）。签名漂移由 mypy strict 兜住。
        notify: 可选异步进度通知回调（签名
            ``async def notify(method: str, params: dict) -> None``，来自
            ``WorkerState.notify``）。测试内嵌调用未注入时为 ``None``，
            handler 侧静默跳过。
        worker_state: 反向引用 :class:`~worker.runtime.state.WorkerState`
            （TYPE_CHECKING 前向引用，运行时 Any 避免循环导入）。
            ``RestoreWorkspace`` 等替换 DB 连接的 handler 需要同步回写
            ``state.db_conn``，否则下一次 dispatch 仍拿到已关闭的旧连接。
    """

    repos: Repos
    ingest: Any = None  # 模块对象；无结构契约可标注
    asr: ASRProvider | None = None
    ai: AIProvider | None = None
    tts: TTSProvider | None = None
    image: ImageProvider | None = None
    #: 发布 Provider（S7）。生产由主装配注入，handler 回落到
    #: ``providers.resolve.resolve_publish_provider()``
    publish: PublishProvider | None = None
    renderer: RendererProvider | None = None
    scene_detector: SceneDetector | None = None
    notify: Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]] | None = None
    worker_state: WorkerState | None = None


async def build_deps(
    state: WorkerState, workspace_id: str | None = None
) -> Deps:
    """两条装配路径共用的**唯一** :class:`Deps` 构造入口。

    - Provider 六件套走进程级缓存
      :func:`worker.runtime.providers.resolve.get_provider_bundle` ——
      缓存命中即复用同一实例（Whisper 模型不重复加载、``shutil.which``
      不重复扫盘）；未命中的构建含**阻塞 IO**，整体丢
      ``asyncio.to_thread`` 避免冷启动第一条重命令冻结事件循环。
    - ``notify`` / ``worker_state`` 一并注入 —— 此前 ``app.run_command``
      漏注导致 CLI/MCP 路径下 9 个 handler 的 ``deps.notify`` 静默 no-op、
      ``RestoreWorkspace`` 的 ``worker_state.db_conn`` 回写被
      ``if is not None`` 悄悄跳过；两条装配路径必须一致。
    - 调用方：Rust sidecar ``handlers.commands.handle_command`` 与
      进程内门面 ``worker.runtime.app.run_command``。
    """
    # 局部 import 避免与 providers.resolve 的循环依赖
    from worker.runtime.providers.resolve import get_provider_bundle

    if state.db_conn is None:
        raise RuntimeError("build_deps 需要已 bootstrap 的 state.db_conn")
    bundle = await asyncio.to_thread(get_provider_bundle, workspace_id)
    return Deps(
        repos=Repos(state.db_conn),
        ingest=_ingest_module,
        asr=bundle["asr"],
        ai=bundle["ai"],
        tts=bundle["tts"],
        image=bundle["image"],
        renderer=bundle["renderer"],
        scene_detector=bundle["scene_detector"],
        notify=state.notify,
        worker_state=state,
    )
