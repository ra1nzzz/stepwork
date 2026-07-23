"""W9 L.41：日志配置模块测试。

覆盖 :mod:`worker.runtime.logging_config`：

1. ``test_configure_logging_creates_log_file``：调用后 ``worker.log`` 存在且内容写入。
2. ``test_configure_logging_writes_json_lines``：每行可 ``json.loads``，含
   ``ts`` / ``level`` / ``name`` / ``msg`` 四字段。
3. ``test_configure_logging_masks_secrets``（**P0 Gate**）：``apiKey=...`` /
   ``token=...`` 形式的密钥值被 ``••••`` 替换，明文不落盘。
4. ``test_configure_logging_fallback_on_permission_error``：``RotatingFileHandler``
   构造抛 ``OSError`` 时降级为仅 stderr，不抛异常。
5. ``test_configure_logging_rotates``：超过 ``max_bytes`` 触发轮转，出现
   ``worker.log.1``。
6. ``test_configure_logging_clears_existing_handlers``：调用后 root 既有 handlers
   被 ``clear``，不叠加。

参考 ``worker/tests/test_diagnostics.py`` 的 ``tmp_path`` + ``monkeypatch`` 模式。
"""

from __future__ import annotations

import json
import logging
import logging.handlers
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from worker.runtime.logging_config import configure_logging


@pytest.fixture
def clean_root_logger() -> Iterator[None]:
    """隔离 root logger：保存现场 → 清空 → 恢复。

    ``configure_logging`` 会 ``clear`` 并重建 root handlers，若不隔离会污染
    其他测试（如 ``test_rpc`` 的 ``caplog``）。本 fixture 同时关闭测试中
    新建的 handler（释放文件句柄，避免 Windows 下 ``ResourceWarning``）。
    """
    root = logging.getLogger()
    original_handlers: list[logging.Handler] = list(root.handlers)
    original_level: int = root.level
    root.handlers.clear()
    try:
        yield
    finally:
        for h in list(root.handlers):
            if h not in original_handlers:
                h.close()
        root.handlers.clear()
        root.handlers.extend(original_handlers)
        root.setLevel(original_level)


def _read_log(log_dir: Path) -> str:
    """读取 ``log_dir/worker.log`` 全文（utf-8）。"""
    return (log_dir / "worker.log").read_text(encoding="utf-8")


def _flush_root_handlers() -> None:
    """flush root logger 所有 handler，确保文件落盘。"""
    for h in logging.getLogger().handlers:
        h.flush()


def test_configure_logging_creates_log_file(
    tmp_path: Path, clean_root_logger: None
) -> None:
    """调用 configure_logging 后 worker.log 存在且能写入日志。"""
    log_dir = tmp_path / "logs"
    log = configure_logging(log_dir)
    log.info("hello world")
    _flush_root_handlers()

    log_file = log_dir / "worker.log"
    assert log_file.exists()
    assert "hello world" in _read_log(log_dir)


def test_configure_logging_writes_json_lines(
    tmp_path: Path, clean_root_logger: None
) -> None:
    """日志格式为 JSON 行：每行可 json.loads，含 ts/level/name/msg。"""
    log_dir = tmp_path / "logs"
    log = configure_logging(log_dir)
    log.info("line one")
    log.warning("line two")
    _flush_root_handlers()

    content = _read_log(log_dir)
    lines = [ln for ln in content.splitlines() if ln.strip()]
    assert len(lines) >= 2

    parsed = [json.loads(ln) for ln in lines]
    for obj in parsed:
        assert {"ts", "level", "name", "msg"} <= set(obj)
    assert any(o["msg"] == "line one" and o["level"] == "INFO" for o in parsed)
    assert any(o["msg"] == "line two" and o["level"] == "WARNING" for o in parsed)


def test_configure_logging_masks_secrets(
    tmp_path: Path, clean_root_logger: None
) -> None:
    """apiKey=... / token=... 形式的密钥值被掩码，明文不落盘。"""
    log_dir = tmp_path / "logs"
    log = configure_logging(log_dir)
    log.info("apiKey=sk-real-secret-12345 token=abc")
    _flush_root_handlers()

    content = _read_log(log_dir)
    assert "sk-real-secret-12345" not in content
    assert "token=abc" not in content
    assert "••••" in content


def test_configure_logging_fallback_on_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_root_logger: None
) -> None:
    """RotatingFileHandler 构造抛 OSError 时降级为仅 stderr，不抛异常。"""

    def _boom(self: Any, *args: Any, **kwargs: Any) -> None:
        raise OSError("mock permission denied")

    monkeypatch.setattr(logging.handlers.RotatingFileHandler, "__init__", _boom)

    # 不应抛异常
    configure_logging(tmp_path / "logs")

    root = logging.getLogger()
    # 仅 stderr handler（无 RotatingFileHandler）
    assert len(root.handlers) == 1
    assert not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers)
    assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)


def test_configure_logging_rotates(
    tmp_path: Path, clean_root_logger: None
) -> None:
    """超过 max_bytes 触发轮转，出现 worker.log.1。"""
    log_dir = tmp_path / "logs"
    log = configure_logging(log_dir, max_bytes=100, backup_count=3)
    # 每行 JSON 约 120+ 字节 > 100，写若干行必然触发轮转
    for _ in range(20):
        log.info("x" * 50)
    _flush_root_handlers()

    assert (log_dir / "worker.log").exists()
    assert (log_dir / "worker.log.1").exists()


def test_configure_logging_clears_existing_handlers(
    tmp_path: Path, clean_root_logger: None
) -> None:
    """configure_logging 会 clear root 既有 handlers，不叠加。"""
    root = logging.getLogger()
    dummy = logging.Handler()
    root.addHandler(dummy)
    assert dummy in root.handlers

    configure_logging(tmp_path / "logs")

    assert dummy not in root.handlers
