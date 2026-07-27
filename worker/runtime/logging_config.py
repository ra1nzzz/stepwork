"""日志配置：stderr + 落盘 RotatingFileHandler（W9 L.41）。

职责：

- :func:`configure_logging`：配置 root logger，同时输出到 stderr（保留 W8 的
  JSON 行格式）与 ``$STEPWORK_HOME/logs/worker.log``（RotatingFileHandler，
  5 MB × 3 份）。文件 handler 创建失败时降级为仅 stderr，不阻塞 worker 启动。
- 密钥脱敏：:class:`MaskingFormatter` 在 ``format()`` 阶段对**格式化后的整行**
  做 ``key=value`` / ``key: value`` 形式的密钥模式掩码，避免密钥明文落盘或
  打到 stderr。

设计取舍（W9_PLAN §8 实现笔记）：

- ``config._mask_secrets`` 仅递归处理 dict / list，对 ``str`` 原值返回（不脱敏），
  故日志行脱敏不能直接复用它。这里用 :func:`_mask_log_str` 做正则掩码，
  覆盖 ``apiKey`` / ``api_key`` / ``api-key`` / ``secret`` / ``token`` /
  ``password`` 等关键字后跟 ``:`` 或 ``=`` 的片段。
- 选择自定义 ``Formatter`` 而非 ``logging.Filter``：Filter 在格式化前改写
  ``record.msg`` 会破坏 ``%``-参数化日志（``msg % args`` 在 ``%s`` 被替换为
  掩码后抛 ``TypeError``）；Formatter 在格式化后改写整行，对参数化日志安全。
- 掩码符号沿用 :func:`config._mask_secrets` 的 ``"••••"``，保持诊断包与日志
  视觉一致。

W8 的 ``__main__._configure_logging`` 仅走 stderr，``diagnostics._collect_recent_logs``
读 ``worker.log`` 始终为空；本模块兑现 W8_PLAN D5 的 P1 后置落盘改造。
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
import sys
from pathlib import Path

__all__ = ["configure_logging"]

# 掩码符号（与 config._mask_secrets 保持一致）
_MASK: str = "••••"

# 匹配 ``keyword[:=]value`` 形式的敏感片段。
# PRD §11.3 明确要求「日志不包含 Cookie、Token、二维码和验证码」——
# 此前只覆盖 api_key/secret/token/password，cookie / 二维码 / 验证码
# 三类全部漏网，故一并纳入（含中文关键字，日志里常直接写中文）。
# 仅当关键字后紧跟 ``:`` 或 ``=``（允许两侧空白）时触发，避免误伤普通叙述。
_SECRET_PATTERN: re.Pattern[str] = re.compile(
    r"(?i)("
    r"api[_-]?key|secret|token|password|passwd|"
    r"cookie|set-cookie|session[_-]?id|authorization|bearer|"
    r"qr[_-]?code|qrcode|captcha|verify[_-]?code|otp|"
    r"二维码|验证码|密码|凭据"
    # 关键字与分隔符之间允许一个引号：JSON 形态是 ``"apiKey": "sk-..."``，
    # 不放行这个引号的话，日志里所有 JSON 形式的密钥都掩不掉 —— 而结构化
    # 日志恰恰全是 JSON。
    r")[\"']?\s*[:=]\s*"
    # 值部分：允许先跟一个认证方案词（Bearer / Basic / Token）再跟真实凭据，
    # 否则 "Authorization: Bearer <token>" 只会掩掉 "Bearer"，真 token 泄漏。
    # 带引号的值整段吃掉，避免只掩到闭合引号之前。
    r"(?:(?:bearer|basic|token|digest)\s+)?(?:\"[^\"]*\"|'[^']*'|\S+)"
)

# data URI 形式的二维码/截图（``data:image/png;base64,...``）：整段抹掉。
# 二维码常以内联图片出现在日志里，上面的 keyword[:=] 规则抓不到。
_DATA_URI_PATTERN: re.Pattern[str] = re.compile(
    r"(?i)data:image/[a-z.+-]+;base64,[A-Za-z0-9+/=]+"
)

# JSON 行格式（与 W8 ``_configure_logging`` 的 basicConfig format 完全一致）
_JSON_LINE_FMT: str = (
    '{"ts":"%(asctime)s","level":"%(levelname)s",'
    '"name":"%(name)s","msg":"%(message)s"}'
)

# RotatingFileHandler 默认参数（W9_PLAN §8 契约：5 MB × 3 份）
_DEFAULT_MAX_BYTES: int = 5 * 1024 * 1024
_DEFAULT_BACKUP_COUNT: int = 3


def _resolve_log_dir() -> Path:
    """解析日志目录：``$STEPWORK_HOME/logs``，缺省 ``~/STEPWORK/logs``。"""
    home = os.environ.get("STEPWORK_HOME") or str(Path.home() / "STEPWORK")
    return Path(home) / "logs"


def mask_secrets(s: str) -> str:
    """公开别名：对任意字符串做 §11.3 掩码。

    日志之外也需要它 —— 例如外部 MCP Server 的 stderr 要回显给用户排查，
    但那段文本很可能含 ``api_key=sk-...``，不能原样透出。
    """
    return _mask_log_str(s)


def _mask_log_str(s: str) -> str:
    """对格式化后的日志字符串做敏感信息掩码（PRD §11.3）。

    两类：``keyword[:=]value`` 片段（含 cookie / 二维码 / 验证码）与
    内联 ``data:image/...;base64,`` 二维码/截图。掩码幂等：对已掩码的
    字符串再次应用不会改变结果。
    """
    masked = _SECRET_PATTERN.sub(lambda m: f"{m.group(1)}={_MASK}", s)
    return _DATA_URI_PATTERN.sub(_MASK, masked)


class MaskingFormatter(logging.Formatter):
    """格式化后对整行做密钥掩码的 Formatter。

    先委托 :meth:`logging.Formatter.format` 完成标准格式化（含 ``%``-参数化
    消息展开），再对结果字符串应用 :func:`_mask_log_str`，确保参数化日志中
    的密钥值也被掩码。
    """

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        return _mask_log_str(formatted)


def configure_logging(
    log_dir: Path | None = None,
    *,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    backup_count: int = _DEFAULT_BACKUP_COUNT,
) -> logging.Logger:
    """配置 root logger：stderr + RotatingFileHandler（JSON 行 + 密钥掩码）。

    Args:
        log_dir: 日志目录；``None`` 时走 :func:`_resolve_log_dir`
            （``$STEPWORK_HOME/logs`` 或 ``~/STEPWORK/logs``）。
        max_bytes: 单个日志文件最大字节数，超过即轮转。
        backup_count: 保留的历史日志份数（``worker.log.1`` … ``worker.log.N``）。

    文件 handler 创建失败（目录不可创建 / 文件不可打开等 ``OSError``）时
    降级为仅 stderr，不抛异常——不阻塞 worker 启动。

    Returns:
        ``worker.runtime`` logger（供调用方记日志）。
    """
    directory = log_dir if log_dir is not None else _resolve_log_dir()
    formatter = MaskingFormatter(_JSON_LINE_FMT)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)

    file_handler: logging.handlers.RotatingFileHandler | None = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        log_path = directory / "worker.log"
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
    except OSError:
        # 降级：目录不可创建或文件不可写时仅走 stderr，不阻塞启动
        file_handler = None

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # 清理现有 handlers，避免 basicConfig / 上次调用 / pytest caplog 叠加
    root.handlers.clear()
    root.addHandler(stderr_handler)
    if file_handler is not None:
        root.addHandler(file_handler)

    return logging.getLogger("worker.runtime")
