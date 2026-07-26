"""发布授权与结果留证（PRD-PUB-004/005）。

授权与账号、内容哈希、插件版本三者绑定，且绑定在**发布时真正生效** ——
只看 status 等于把哈希当装饰（申请 → 批准 → 偷改标题 → 照样能发）。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.handlers.approvals import content_hash as approval_content_hash
from worker.runtime.handlers.approvals import create_request as create_approval
from worker.runtime.handlers.publish_common import (
    _PUBLISH_STATES,
    _authorization_error,
    _now,
    _publish_job_to_dict,
)
from worker.runtime.models import CommandEnvelope, CommandResult


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    repos = deps.repos
    p: dict[str, Any] = env.payload or {}

    if env.commandType == "RequestPublishAuthorization":
        # PRD-PUB-004「一次性发布授权：授权与账号、内容哈希、插件版本绑定」。
        # publish_jobs.approval_id 列 0003 就有，但全仓无任何写入方。
        variant_id = p.get("variantId") or p.get("variant_id")
        if not variant_id:
            raise DispatchError("INVALID_ARGUMENT", "variantId required")
        row = repos.conn.execute(
            "SELECT * FROM platform_variants WHERE id=?", (str(variant_id),)
        ).fetchone()
        if row is None:
            raise DispatchError("NOT_FOUND", f"variant {variant_id!r} not found")

        account_id = p.get("socialAccountId") or p.get("social_account_id")
        plugin_id = p.get("pluginId") or p.get("plugin_id")
        plugin_version = p.get("pluginVersion") or p.get("plugin_version")

        # 绑定内容：变体的标题/正文/标签一起入哈希，任一改动都会让授权失效
        bound_content = {
            "variant_id": str(variant_id),
            "platform": row["platform"],
            "title": row["title"],
            "body": row["body"],
            "tags": row["tags"],
            "social_account_id": account_id,
            "plugin_id": plugin_id,
            "plugin_version": plugin_version,
        }
        approval_id = create_approval(
            repos.conn,
            actor=f"{(env.actor or {}).get('type', 'user')}:"
            f"{(env.actor or {}).get('id', 'unknown')}",
            action_type="PublishVariant",
            target=str(variant_id),
            requested_scope="once",
            risk_summary=(
                f"发布到 {row['platform']}"
                + (f"（账号 {account_id}）" if account_id else "")
                + "；发布后内容将对外可见，且平台侧撤回需自行操作。"
            ),
            payload=bound_content,
        )

        job_id = f"pubjob_{uuid.uuid4().hex}"
        now = _now()
        repos.conn.execute(
            "INSERT INTO publish_jobs "
            "(id, platform_variant_id, social_account_id, plugin_id, plugin_version, "
            "state, approval_id, evidence_artifact_ids, remote_content_id, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                job_id, str(variant_id), account_id, plugin_id, plugin_version,
                "awaiting_approval", approval_id, "[]", None, now, now,
            ),
        )
        repos.conn.commit()
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={
                "publish_job_id": job_id,
                "approval_id": approval_id,
                "content_hash": approval_content_hash(bound_content),
                "state": "awaiting_approval",
            },
        )

    if env.commandType == "RecordPublishResult":
        # PRD-PUB-005「发布结果验证和证据：保存状态、时间和脱敏截图」。
        # 独立命名，避免与上文生成的 str job_id 复用同名（mypy no-redef 同型）
        target_job = p.get("publishJobId") or p.get("publish_job_id")
        state = str(p.get("state") or "")
        if not target_job:
            raise DispatchError("INVALID_ARGUMENT", "publishJobId required")
        if state not in _PUBLISH_STATES:
            raise DispatchError(
                "INVALID_ARGUMENT",
                f"state must be one of {sorted(_PUBLISH_STATES)}",
            )
        job_row = repos.conn.execute(
            "SELECT * FROM publish_jobs WHERE id=?", (str(target_job),)
        ).fetchone()
        if job_row is None:
            raise DispatchError("NOT_FOUND", f"publish job {target_job!r} not found")

        # 发布必须先获批，且授权仍与**当前**内容匹配（PRD-PUB-004）
        if state == "published":
            variant_row = repos.conn.execute(
                "SELECT * FROM platform_variants WHERE id=?",
                (job_row["platform_variant_id"],),
            ).fetchone()
            if variant_row is None:
                raise DispatchError("NOT_FOUND", "platform variant not found")
            current_bound = {
                "variant_id": str(job_row["platform_variant_id"]),
                "platform": variant_row["platform"],
                "title": variant_row["title"],
                "body": variant_row["body"],
                "tags": variant_row["tags"],
                "social_account_id": job_row["social_account_id"],
                "plugin_id": job_row["plugin_id"],
                "plugin_version": job_row["plugin_version"],
            }
            reason = _authorization_error(
                repos.conn, job_row["approval_id"], current_bound
            )
            if reason:
                raise DispatchError("FORBIDDEN", reason)

        evidence = p.get("evidenceArtifactIds") or p.get("evidence_artifact_ids") or []
        if not isinstance(evidence, list) or not all(
            isinstance(e, str) for e in evidence
        ):
            raise DispatchError(
                "INVALID_ARGUMENT", "evidenceArtifactIds must be a list of strings"
            )
        repos.conn.execute(
            "UPDATE publish_jobs SET state=?, evidence_artifact_ids=?, "
            "remote_content_id=?, updated_at=? WHERE id=?",
            (
                state,
                json.dumps(evidence, ensure_ascii=False),
                p.get("remoteContentId") or p.get("remote_content_id"),
                _now(),
                str(target_job),
            ),
        )
        # 一次性授权用掉即作废（PRD-PUB-004）
        if state == "published" and job_row["approval_id"]:
            repos.conn.execute(
                "UPDATE approval_requests SET status='consumed' "
                "WHERE id=? AND requested_scope='once'",
                (job_row["approval_id"],),
            )
        repos.conn.commit()
        updated = repos.conn.execute(
            "SELECT * FROM publish_jobs WHERE id=?", (str(target_job),)
        ).fetchone()
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"publish_job": _publish_job_to_dict(updated)},
        )

    if env.commandType == "ListPublishJobs":
        pid = p.get("projectId") or p.get("project_id") or env.projectId
        sql = (
            "SELECT pj.* FROM publish_jobs pj "
            "JOIN platform_variants pv ON pv.id = pj.platform_variant_id"
        )
        args: list[Any] = []
        if pid:
            sql += " WHERE pv.project_id=?"
            args.append(str(pid))
        sql += " ORDER BY pj.created_at DESC"
        rows = repos.conn.execute(sql, tuple(args)).fetchall()
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"publish_jobs": [_publish_job_to_dict(r) for r in rows]},
        )

    raise DispatchError(
        "UNKNOWN_COMMAND",
        f"commandType {env.commandType!r} not handled by publish handler",
    )

    raise DispatchError(
        "UNKNOWN_COMMAND",
        f"commandType {env.commandType!r} not handled by this publish submodule",
    )
