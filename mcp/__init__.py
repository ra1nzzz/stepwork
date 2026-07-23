"""STEPWORK MCP server package (W7 Phase 3).

See :mod:`mcp.server` for the stdio JSON-RPC implementation. This package
exposes a fixed set of *read-only* tools over the worker Command Bus; it
never exposes ``update_config`` / ``UpdateConfig`` (secret-write).
"""

from __future__ import annotations

__all__ = ["server"]
