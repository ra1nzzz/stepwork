"""Dev bridge：在缺少 Rust sidecar 的纯浏览器开发环境里，把 GUI 的
``CommandEnvelope`` 转发到**真实**的 Python worker Command Bus（``bus.dispatch``）。

这解决了沙箱里无法编译/显示 Tauri 二进制、但又要让 MVP 功能真正可用的问题：
浏览器版 GUI（`VITE_DEV_BRIDGE=1 npm run dev`）经此桥直接调用真实后端，
而非 ``lib/tauri.ts`` 里的 mock。

启动（仓库根目录执行）：
    python worker/dev_bridge.py
    STEPWORK_DEV_BRIDGE_PORT=9000 python worker/dev_bridge.py

前端配合（apps/desktop）：
    VITE_DEV_BRIDGE=1 npm run dev
    # lib/tauri.ts 会把 dispatchCommand / getWorkerHealth 改为请求本服务

仅用于本地开发/演示，不要用于生产。
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import sqlite3
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from worker.runtime.bootstrap import MIGRATIONS_DIR
from worker.runtime.db.migrations import run_migrations
from worker.runtime.handlers import commands
from worker.runtime.state import WorkerState

# 单一 WorkerState（含已初始化的 db_conn），全程复用。
STATE = WorkerState()
_DB_PATH = os.environ.get("STEPWORK_DEV_DB") or os.path.join(
    tempfile.gettempdir(), "stepwork-devbridge.db"
)
try:
    # 复用生产 connect()（WAL+外键+Row factory），但放开同线程限制：
    # ThreadingHTTPServer 在worker 线程处理请求，而连接在主线程创建，
    # 必须 check_same_thread=False，并用锁串行化访问，避免跨线程 SQLite 报错。
    _conn = sqlite3.connect(_DB_PATH, check_same_thread=False, timeout=30)
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    _conn.row_factory = sqlite3.Row
    run_migrations(_conn, MIGRATIONS_DIR)
    STATE.db_conn = _conn
    STATE.db_path = _DB_PATH
except Exception as exc:  # pragma: no cover - 启动期依赖缺失时给出清晰报错
    raise SystemExit(f"[dev_bridge] 初始化数据库失败: {exc}")

# SQLite 连接非线程安全，用锁串行化所有请求（本地开发并发极低，足够）。
_DB_LOCK = threading.Lock()


def _to_command_result(payload: dict) -> dict:
    """把 ``handle_command`` 的返回归一为前端期望的 ``CommandResult`` 形状。"""
    if "result" in payload:
        return payload["result"]
    err = payload.get("error", {})
    return {
        "ok": False,
        "commandId": None,
        "job_id": None,
        "artifact_ids": [],
        "error": err.get("message", "bridge_error"),
        "detail": None,
    }


async def _dispatch_envelope(envelope: dict) -> dict:
    params = {"envelope": envelope}
    payload = await commands.handle_command(params, STATE)
    return _to_command_result(payload)


def _health() -> dict:
    return {
        "status": "ok",
        "version": "0.1.0-bridge",
        "protocol_version": "1",
        "uptime_seconds": 0,
        "pid": os.getpid(),
        "last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
        "startup_duration_ms": 0,
        "active_jobs": 0,
        "degraded_reasons": [],
        "runtime_info": {
            "python_version": platform.python_version(),
            "sqlite_version": sqlite3.sqlite_version,
            "platform": "dev-bridge",
        },
    }


class _Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # 静默
        return

    def do_OPTIONS(self) -> None:
        self._send(204, {})

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/health":
            self._send(200, _health())
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/dispatch":
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length else b"{}"
            envelope = json.loads(raw or b"{}")
        except Exception as exc:
            self._send(400, {"ok": False, "error": f"bad body: {exc}"})
            return
        try:
            with _DB_LOCK:
                result = asyncio.run(_dispatch_envelope(envelope))
        except Exception as exc:
            self._send(500, {"ok": False, "error": f"bridge internal: {exc}"})
            return
        self._send(200, result)


def main() -> None:
    port = int(os.environ.get("STEPWORK_DEV_BRIDGE_PORT", "8787"))
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    print(f"[dev_bridge] listening on http://127.0.0.1:{port}  (db={_DB_PATH})")
    print("[dev_bridge] POST /dispatch  {CommandEnvelope} -> CommandResult")
    print("[dev_bridge] GET  /health")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
