"""``EditParagraph`` 命令处理（PRD-SCR-003）。

段落级生成 / 重写 / 扩写 / 压缩。职责：

1. 取源脚本版本 → 切段 → 定位目标段（``paragraph_index``）
2. 组段落级 prompt（带上下段上下文）→ 调 AI Provider
3. **只替换目标段**，其余段落原样保留
4. 结果落成新的 ``content_versions(script)``，``parent_version_id`` 指向
   源版本 —— 因此「所有操作可撤销」= 回滚到 parent（与既有版本链一致）

脚本正文可能是纯文本，也可能是 ``{"title","body"}`` JSON（GenerateScript
的产出）。两种形态都支持：JSON 形态只对 ``body`` 做段落操作，标题不动。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from worker.runtime.audit import build_invocation, record_provider_invocation
from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.handlers.brand import (
    brand_producer_fields,
    format_brand_prompt_block,
    load_project_brand,
)
from worker.runtime.models import CommandEnvelope, CommandResult, ContentVersion
from worker.runtime.providers.resolve import ai_provider_from_hint
from worker.runtime.script.paragraph import (
    OPERATIONS,
    PARAGRAPH_SCHEMA,
    build_paragraph_prompt,
    parse_paragraph_result,
    replace_paragraph,
    split_paragraphs,
)


def _load_body(content: str) -> tuple[str, dict[str, Any] | None]:
    """解析脚本内容 → ``(正文, JSON 包装)``。

    纯文本形态返回 ``(content, None)``；``{"title","body"}`` 形态返回
    ``(body, 原始 dict)``，便于替换后按原形态写回。
    """
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        return content, None
    if isinstance(parsed, dict) and isinstance(parsed.get("body"), str):
        return parsed["body"], parsed
    return content, None


def _dump_body(body: str, wrapper: dict[str, Any] | None) -> str:
    """按原形态写回（JSON 形态保留 title 等同级字段）。"""
    if wrapper is None:
        return body
    updated = dict(wrapper)
    updated["body"] = body
    return json.dumps(updated, ensure_ascii=False)


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """处理 ``EditParagraph``。"""
    repos = deps.repos
    p: dict[str, Any] = env.payload or {}

    operation = str(p.get("operation") or "").lower()
    if operation not in OPERATIONS:
        raise DispatchError(
            "INVALID_ARGUMENT",
            f"operation must be one of {sorted(OPERATIONS)}, got {operation!r}",
        )

    version_id = p.get("version_id") or p.get("versionId")
    if not version_id:
        raise DispatchError("INVALID_ARGUMENT", "version_id required")

    index = p.get("paragraph_index", p.get("paragraphIndex"))
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise DispatchError(
            "INVALID_ARGUMENT",
            f"paragraph_index must be a non-negative integer, got {index!r}",
        )

    ai = ai_provider_from_hint(p.get("provider")) or deps.ai
    if ai is None:
        raise DispatchError("UNAVAILABLE", "ai provider not configured")

    project_id = env.projectId or repos.projects.get_or_create_default(
        env.workspaceId
    ).id
    src = repos.content_versions.get(str(version_id))
    if src is None or src.project_id != project_id:
        raise DispatchError("NOT_FOUND", f"version {version_id!r} not found")

    body, wrapper = _load_body(src.content or "")
    paragraphs = split_paragraphs(body)
    if not paragraphs:
        raise DispatchError("INVALID_ARGUMENT", "source script has no paragraphs")
    if index >= len(paragraphs):
        raise DispatchError(
            "INVALID_ARGUMENT",
            f"paragraph_index {index} out of range (0..{len(paragraphs) - 1})",
        )

    # PRD-BRD-002：段落级改写同样尊重「生成时可选择启用」
    use_brand = p.get("use_brand_profile", True)
    brand = load_project_brand(repos, project_id) if use_brand else None
    brand_block = format_brand_prompt_block(brand) if brand else None

    prompt = build_paragraph_prompt(
        operation,
        paragraphs[index],
        before=paragraphs[index - 1] if index > 0 else None,
        after=paragraphs[index + 1] if index + 1 < len(paragraphs) else None,
        instruction=p.get("instruction"),
        brand_block=brand_block,
    )

    try:
        raw = await ai.complete(prompt, PARAGRAPH_SCHEMA)
        new_paragraph = parse_paragraph_result(raw)
    except DispatchError:
        raise
    except Exception as e:
        raise DispatchError("EDIT_FAILED", f"paragraph edit failed: {e}") from None

    new_body = replace_paragraph(body, index, new_paragraph)
    content_str = _dump_body(new_body, wrapper)

    cv = ContentVersion(
        project_id=project_id,
        parent_version_id=src.id,
        content_type="script",
        content=content_str,
        content_hash=hashlib.sha256(content_str.encode("utf-8")).hexdigest(),
        producer={
            "kind": "ai-paragraph-edit",
            "provider": getattr(ai, "name", "unknown"),
            "operation": operation,
            "paragraph_index": index,
            **brand_producer_fields(brand),
        },
    )
    cv_id = repos.content_versions.insert(cv)

    # 费用透明（PRD-ANA-006 同款）：detail.invocation + 审计行
    invocation = build_invocation(ai, len(prompt) + len(new_paragraph))
    record_provider_invocation(repos.conn, env, invocation)

    return CommandResult(
        ok=True,
        commandId=env.commandId,
        artifact_ids=[cv_id],
        detail={
            "version_id": cv_id,
            "parent_version_id": src.id,
            "operation": operation,
            "paragraph_index": index,
            "paragraph_count": len(split_paragraphs(new_body)),
            "paragraph_before": paragraphs[index],
            "paragraph_after": new_paragraph,
            "invocation": invocation,
        },
    )
