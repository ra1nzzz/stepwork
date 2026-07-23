"""``config`` 子命令辅助（W7 Phase 3）。

读取配置：``config get`` → ``GetConfig``（payload ``{}``）。
写入配置：``config set --file <path.json> | --stdin`` → ``UpdateConfig``，
payload 为解析后的 JSON 对象。

安全约束（三角色 P0 / SET.7）：
- 密钥（``*Key`` / ``*Secret`` / ``*Token`` / ``*Password``）**绝不**通过
  CLI 参数传递——只能经由 ``--file`` / ``--stdin`` / 环境变量进入。
- 结果输出中对密钥一律走后端掩码视图，CLI 永不回显明文密钥。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def add_config_subcommands(sub: Any) -> None:
    """挂接 ``config`` 子命令树到给定 subparsers。"""
    cfg = sub.add_parser("config", help="读取 / 写入工作区配置")
    cfg_sub = cfg.add_subparsers(dest="config_action", required=True)

    get_p = cfg_sub.add_parser("get", help="读取合并后的配置（密钥已掩码）")
    get_p.set_defaults(command_type="GetConfig")

    set_p = cfg_sub.add_parser(
        "set", help="写入配置（仅 --file / --stdin；不接收明文密钥）"
    )
    set_p.set_defaults(command_type="UpdateConfig")
    # 互斥且必填：配置来源只能是文件或标准输入，二者之一。
    # 刻意不提供任何接收明文密钥的参数（如 --api-key），密钥只能经由
    # --file / --stdin 的文件内容或进程环境变量进入。
    src = set_p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--file",
        metavar="PATH",
        help="从 JSON 文件读取配置（推荐用于含密钥的配置）",
    )
    src.add_argument(
        "--stdin",
        action="store_true",
        help="从标准输入读取 JSON 配置",
    )


def load_config_payload(args: argparse.Namespace) -> dict[str, Any]:
    """从 ``--file`` 或 ``--stdin`` 读取配置 JSON，作为 ``UpdateConfig`` 的 payload。

    Raises:
        ValueError: 文件缺失 / JSON 非法 / 顶层不是对象。
    """
    if getattr(args, "stdin", False):
        raw = sys.stdin.read()
    else:
        path = getattr(args, "file", None)
        if not path:
            raise ValueError("config set requires --file <path> or --stdin")
        with open(path, encoding="utf-8") as f:
            raw = f.read()

    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid config JSON: {e}") from e

    if not isinstance(data, dict):
        raise ValueError("config payload must be a JSON object")
    return data


def config_payload(args: argparse.Namespace) -> dict[str, Any]:
    """根据 ``config`` 子命令的动作返回对应 payload。"""
    action = getattr(args, "config_action", None)
    if action == "get":
        return {}
    if action == "set":
        return load_config_payload(args)
    raise ValueError(f"unknown config action: {action!r}")
