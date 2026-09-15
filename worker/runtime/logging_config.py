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

import json
import logging
import logging.handlers
import os
import re
import sys
from pathlib import Path

__all__ = ["configure_logging"]

# 掩码符号（与 config._mask_secrets 保持一致）
_MASK: str = "••••"

# 关键字清单 —— 必须与 :data:`worker.runtime.handlers.config._SECRET_RE` 覆盖的
# 字段名保持同步（``passphrase`` / ``credential`` / 裸 ``key`` 曾在配置侧被剥离
# 不落库，日志侧却漏掩，导致 ``UpdateConfig`` payload 摘要里的这些字段明文进
# ``worker.log`` 并被 ``diagnostics._collect_recent_logs`` 打进诊断包）。
# 另加仅出现在日志文本 / HTTP 头 / 内联图片里的凭据类关键词（PRD §11.3 明确要求
# 「日志不包含 Cookie、Token、二维码和验证码」）。
# 交替式按最长优先排：先 ``access[_-]?key`` 再 ``key``，避免只匹配到尾部。
_KEYWORD_ALT: str = (
    "api[_-]?key|access[_-]?key|secret[_-]?key|private[_-]?key|refresh[_-]?key|"
    "session[_-]?id|set[_-]?cookie|verify[_-]?code|qr[_-]?code|"
    "password|passwd|passphrase|credential|authorization|bearer|"
    "secret|token|cookie|apikey|qrcode|captcha|otp|key|"
    "二维码|验证码|密码|凭据"
)

# 匹配 ``keyword <quote?> <sep> <auth?> <value>`` 形式的敏感片段。
# - ``(?<![\w-])`` 左边界：避免 ``monkey=1`` 之类误伤（前一个是词字符或连字符就不算关键字起点）。
# - ``(?P<q>[\"']?)`` 捕获关键字后可能出现的引号（JSON 形态是 ``"apiKey": "..."``），
#   回填时原样保留，防止整行 JSON 被掩码打碎。
# - value 分三种：双引号 / 单引号 / 裸值（收紧到 JSON 定界符前，避免 ``\S+`` 吃掉 ``}``）。
# - 允许值前先跟 ``Bearer/Basic/Token/Digest`` 认证方案词 —— 否则 ``Authorization: Bearer <t>``
#   只掩掉 ``Bearer``，真 token 泄漏。
_SECRET_PATTERN: re.Pattern[str] = re.compile(
    r"(?i)(?<![\w-])(?P<kw>" + _KEYWORD_ALT + r")"
    r"(?P<q>[\"']?)(?P<sep>\s*[:=]\s*)"
    r"(?P<auth>(?:bearer|basic|token|digest)\s+)?"
    r'(?P<val>"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|[^\s,}\]\'"]+)'
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

    替换**只吃掉 value 部分**，把 keyword / 两侧引号 / ``:`` 或 ``=`` 分隔符 /
    认证方案词原样回填 —— 这样结构化日志里
    ``{"apiKey": "sk-live-x"}`` 掩码后是 ``{"apiKey": "••••"}``（仍是合法 JSON）。
    旧实现把 ``apiKey": "sk-live-x"`` 整段换成 ``apiKey=••••``，引号被吞、
    JSON 结构碎裂，``_JSON_LINE_FMT`` 把 message 裸插进 ``"msg":"…"`` 后
    每条含 payload 的日志都无法 ``json.loads``（W9 归档里的 P1）。

    掩码符号本身不含引号，对已掩码串再跑一次匹配到的 value 是 ``••••``（裸值
    分支），会被替换成同一 ``••••`` —— 幂等性保持。
    """

    def _repl(m: re.Match[str]) -> str:
        raw_val = m.group("val")
        if raw_val.startswith('"'):
            masked = f'"{_MASK}"'
        elif raw_val.startswith("'"):
            masked = f"'{_MASK}'"
        else:
            masked = _MASK
        auth = m.group("auth") or ""
        return f"{m.group('kw')}{m.group('q')}{m.group('sep')}{auth}{masked}"

    masked = _SECRET_PATTERN.sub(_repl, s)
    return _DATA_URI_PATTERN.sub(_MASK, masked)


class MaskingFormatter(logging.Formatter):
    """把日志 record 序列化为**合法** JSON 行，再做密钥掩码。

    两件事同时被修：

    - 旧版 :class:`logging.Formatter` 的 ``%(message)s`` 把消息裸插进
      ``"msg":"…"``，日志正文含引号 / 反斜杠 / 换行 → 整行 JSON 碎裂
      （dimension A 归档里的 P1：结构化日志宣称 grep 得动，实际每条含
      dict payload 的都是非法 JSON）。
    - 掩码若放在 ``json.dumps`` **之后**，``json.dumps`` 已把 msg 里的
      ``"`` 转义成 ``\\"`` —— 掩码正则 ``[\"']?`` 只吃一个引号，遇到
      反斜杠就落空，密钥原样泄漏进磁盘。

    现在掩码在**明文阶段**（msg / exc / stack 各自 ``_mask_log_str``）做，
    再由 ``json.dumps`` 保证整行合法。异常 / stack 也走同一路径 ——
    traceback 里最可能带 token 与 base64 凭据。
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, str] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "name": record.name,
            "msg": _mask_log_str(record.getMessage()),
        }
        if record.exc_info:
            payload["exc"] = _mask_log_str(self.formatException(record.exc_info))
        elif record.stack_info:
            payload["stack"] = _mask_log_str(self.formatStack(record.stack_info))
        return json.dumps(payload, ensure_ascii=False)


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
