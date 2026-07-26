"""命令级可观测性：结构化日志 + correlationId + 本地指标。

改造前 18k 行后端只有 38 处零散日志、0 个指标，出问题基本靠猜：用户说
「渲染很慢」，我们既不知道慢在哪一步，也不知道是不是只有他慢。

三件事：

1. **结构化日志**：每条命令在 dispatch 出口记一行 —— commandType / 耗时 /
   ok / 错误码 / correlationId。一行一条命令，grep 得动。
2. **correlationId**：贯穿 UI → Rust → worker → provider，诊断包里能把一次
   操作的所有痕迹串起来。信封里没带就地生成，不强制上游改造。
3. **本地指标**：命令计数、耗时分位、失败率，直接写 SQLite。

**刻意不引 Prometheus / OpenTelemetry**：这是本地优先的桌面应用，为看几个
计数拉起一套监控栈，用户既装不动也不会去看。指标进库，诊断包一并带走。

日志内容一律过 §11.3 掩码 —— 命令 payload 里可能有 API key。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextvars import ContextVar
from typing import Any

from worker.runtime.logging_config import mask_secrets

logger = logging.getLogger("worker.runtime.command")

#: 当前调用链 id。用 ContextVar 而不是参数透传：provider 调用埋在好几层
#: 之下，逐层加参数会污染所有签名，而且必然有地方忘了传。
_CORRELATION: ContextVar[str] = ContextVar("correlation_id", default="")

#: 单次记录的 payload 摘要长度上限（日志不是数据备份）
_MAX_PAYLOAD_CHARS = 400


def current_correlation_id() -> str:
    """当前调用链 id（不在命令上下文里时为空串）。"""
    return _CORRELATION.get()


def set_correlation_id(value: str) -> str:
    """设置调用链 id，返回设置前的值（供恢复）。"""
    previous = _CORRELATION.get()
    _CORRELATION.set(value)
    return previous


def resolve_correlation_id(raw_envelope: dict[str, Any]) -> str:
    """从信封解析调用链 id。

    优先用上游给的（UI / Rust 会带），没有就用 commandId —— 那本来就是这次
    调用的唯一标识，比再生成一个新的更有用。两者都没有才现造。
    """
    for key in ("correlationId", "correlation_id"):
        value = raw_envelope.get(key)
        if value:
            return str(value)
    command_id = raw_envelope.get("commandId")
    return str(command_id) if command_id else uuid.uuid4().hex


def _payload_digest(payload: Any) -> str:
    """payload 摘要（截断 + 掩码）。

    只记键名和短值：完整 payload 可能含整篇脚本，写进日志既没用又占空间；
    但完全不记又会让「参数长什么样」无从查证，取中间。
    """
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(payload)
    return mask_secrets(text)[:_MAX_PAYLOAD_CHARS]


class CommandTimer:
    """一次命令执行的计时与记录。

    用法::

        with CommandTimer(command_type, correlation_id, payload) as timer:
            ...
            timer.finish(ok=True, error=None)
    """

    def __init__(self, command_type: str, correlation_id: str, payload: Any = None) -> None:
        self.command_type = command_type
        self.correlation_id = correlation_id
        self.payload = payload
        self.started = 0.0
        self.duration_ms = 0.0
        self.ok: bool | None = None
        self.error: str | None = None

    def __enter__(self) -> CommandTimer:
        self.started = time.monotonic()
        self._token = set_correlation_id(self.correlation_id)
        return self

    def __exit__(self, *_exc: object) -> None:
        set_correlation_id(self._token)
        if self.ok is None:
            # 没走到 finish（异常穿透），也要留一条，否则最该看到的那次没记录
            self.finish(ok=False, error="uncaught")

    def finish(self, *, ok: bool, error: str | None) -> None:
        self.duration_ms = round((time.monotonic() - self.started) * 1000, 2)
        self.ok = ok
        self.error = error
        logger.info(
            "command %s ok=%s ms=%s cid=%s err=%s payload=%s",
            self.command_type,
            ok,
            self.duration_ms,
            self.correlation_id,
            (error or "")[:120],
            _payload_digest(self.payload),
        )


def record_metric(conn: Any, timer: CommandTimer) -> None:
    """把一次命令的执行结果写进本地指标表。

    写库失败绝不影响业务结果 —— 指标是观测手段，不该成为新的故障源。
    """
    try:
        conn.execute(
            "INSERT INTO command_metrics "
            "(command_type, ok, duration_ms, error_code, correlation_id, recorded_at) "
            "VALUES (?,?,?,?,?,datetime('now'))",
            (
                timer.command_type,
                1 if timer.ok else 0,
                timer.duration_ms,
                (timer.error or "").split(":")[0][:64] or None,
                timer.correlation_id,
            ),
        )
        conn.commit()
    except Exception:  # noqa: BLE001 - 指标写失败不影响命令结果
        logger.debug("metric write failed for %s", timer.command_type)


def summarize(conn: Any, *, limit: int = 20) -> list[dict[str, Any]]:
    """按命令聚合的指标摘要（次数 / 失败率 / 耗时分位）。

    P50/P95 用 SQLite 的窗口函数算不划算（要么排序全表要么写递归 CTE），
    这里取平均与最大值 —— 桌面端单机量级下够用，真要分位数再说。
    过早引入复杂统计，代价是每次查询都慢，而收益只是数字更好看。
    """
    rows = conn.execute(
        "SELECT command_type, COUNT(*) n, "
        "SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) failures, "
        "ROUND(AVG(duration_ms), 2) avg_ms, MAX(duration_ms) max_ms "
        "FROM command_metrics GROUP BY command_type "
        "ORDER BY n DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {
            "command_type": str(r["command_type"]),
            "count": int(r["n"]),
            "failures": int(r["failures"] or 0),
            "failure_rate": round((r["failures"] or 0) / max(int(r["n"]), 1), 4),
            "avg_ms": float(r["avg_ms"] or 0.0),
            "max_ms": float(r["max_ms"] or 0.0),
        }
        for r in rows
    ]
