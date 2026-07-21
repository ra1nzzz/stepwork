"""仓储层（W3-W4 Batch 0）。

每个 repo 封装一类实体的 CRUD，全部基于同一 ``conn``。``Repos``
聚合并统一注入，确保 Job 引擎与 Command Bus 共享同一事务边界
（三角色头脑风暴 P0：lease 与 payload 写入必须一致）。

INSERT 占位符用 :func:`_q` 生成，列数与值元组长度强一致，
避免手数 ``?`` 出错。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Optional

from worker.runtime.models import (
    ContentProject,
    ContentVersion,
    Job,
    JobStage,
    JobState,
    SourceAsset,
    Workspace,
)


def _q(n: int) -> str:
    """生成 ``n`` 个逗号分隔的 ``?`` 占位符。"""
    return ",".join(["?"] * n)


def _row_to_workspace(row: sqlite3.Row) -> Workspace:
    return Workspace(
        id=str(row["id"]),
        name=str(row["name"]),
        root_path=str(row["root_path"]),
        settings=json.loads(row["settings"]) if row["settings"] else {},
        created_at=str(row["created_at"]),
        archived_at=str(row["archived_at"]) if row["archived_at"] is not None else None,
    )


def _row_to_project(row: sqlite3.Row) -> ContentProject:
    return ContentProject(
        id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        title=str(row["title"]),
        status=str(row["status"]),
        brand_profile_id=(
            str(row["brand_profile_id"]) if row["brand_profile_id"] is not None else None
        ),
        current_content_version_id=(
            str(row["current_content_version_id"])
            if row["current_content_version_id"] is not None
            else None
        ),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_to_source_asset(row: sqlite3.Row) -> SourceAsset:
    return SourceAsset(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        kind=str(row["kind"]),
        local_uri=str(row["local_uri"]),
        original_uri=str(row["original_uri"]) if row["original_uri"] is not None else None,
        content_hash=str(row["content_hash"]),
        rights_declaration=(
            str(row["rights_declaration"]) if row["rights_declaration"] is not None else None
        ),
        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        created_at=str(row["created_at"]),
    )


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=str(row["id"]),
        job_type=str(row["job_type"]),
        state=JobState(str(row["state"])),
        stage=JobStage(str(row["stage"])) if row["stage"] is not None else None,
        payload=json.loads(row["payload"]) if row["payload"] else {},
        progress=float(row["progress"]),
        attempt_count=int(row["attempt_count"]),
        max_attempts=int(row["max_attempts"]),
        lease_owner=str(row["lease_owner"]) if row["lease_owner"] is not None else None,
        lease_expires_at=(
            str(row["lease_expires_at"]) if row["lease_expires_at"] is not None else None
        ),
        heartbeat_at=str(row["heartbeat_at"]) if row["heartbeat_at"] is not None else None,
        error_code=str(row["error_code"]) if row["error_code"] is not None else None,
        result_artifact_ids=(
            json.loads(row["result_artifact_ids"]) if row["result_artifact_ids"] else []
        ),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_to_content_version(row: sqlite3.Row) -> ContentVersion:
    return ContentVersion(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        parent_version_id=(
            str(row["parent_version_id"]) if row["parent_version_id"] is not None else None
        ),
        content_type=str(row["content_type"]),
        content=str(row["content"]),
        content_hash=str(row["content_hash"]),
        producer=json.loads(row["producer"]) if row["producer"] else {},
        created_at=str(row["created_at"]),
    )


class WorkspaceRepo:
    """``workspaces`` 表。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, w: Workspace) -> str:
        cols = "id,name,root_path,settings,created_at,archived_at"
        self.conn.execute(
            f"INSERT INTO workspaces ({cols}) VALUES ({_q(6)})",
            (w.id, w.name, w.root_path, json.dumps(w.settings), w.created_at, w.archived_at),
        )
        self.conn.commit()
        return w.id

    def get_or_create(self, name: str, root_path: str) -> Workspace:
        row = self.conn.execute("SELECT * FROM workspaces WHERE name=?", (name,)).fetchone()
        if row is not None:
            return _row_to_workspace(row)
        w = Workspace(name=name, root_path=root_path)
        self.insert(w)
        return w

    def ensure(
        self, ws_id: str, name: Optional[str] = None, root_path: Optional[str] = None
    ) -> Workspace:
        """确保 ``ws_id`` 对应的工作区行存在（不存在则按 id 插入）。

        导入等命令携带 ``workspaceId``（即 id），上游未必先建工作区；
        此处以 id 为准幂等插入，避免 FK 约束失败。
        """
        cols = "id,name,root_path,settings,created_at,archived_at"
        self.conn.execute(
            f"INSERT OR IGNORE INTO workspaces ({cols}) VALUES ({_q(6)})",
            (ws_id, name or ws_id, root_path or f"STEPWORK_HOME/workspaces/{ws_id}",
             "{}", datetime.now(UTC).isoformat(), None),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM workspaces WHERE id=?", (ws_id,)).fetchone()
        assert row is not None
        return _row_to_workspace(row)


class ProjectRepo:
    """``content_projects`` 表。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, p: ContentProject) -> str:
        cols = (
            "id,workspace_id,title,status,brand_profile_id,"
            "current_content_version_id,created_at,updated_at"
        )
        self.conn.execute(
            f"INSERT INTO content_projects ({cols}) VALUES ({_q(8)})",
            (p.id, p.workspace_id, p.title, p.status, p.brand_profile_id,
             p.current_content_version_id, p.created_at, p.updated_at),
        )
        self.conn.commit()
        return p.id

    def get_or_create_default(self, workspace_id: str) -> ContentProject:
        row = self.conn.execute(
            "SELECT * FROM content_projects WHERE workspace_id=? LIMIT 1", (workspace_id,)
        ).fetchone()
        if row is not None:
            return _row_to_project(row)
        p = ContentProject(workspace_id=workspace_id, title="default")
        self.insert(p)
        return p


class SourceAssetRepo:
    """``source_assets`` 表；``(project_id, content_hash)`` 唯一，导入去重。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert_dedup(self, a: SourceAsset) -> str:
        cols = (
            "id,project_id,kind,local_uri,original_uri,"
            "content_hash,rights_declaration,metadata,created_at"
        )
        try:
            self.conn.execute(
                f"INSERT INTO source_assets ({cols}) VALUES ({_q(9)})",
                (a.id, a.project_id, a.kind, a.local_uri, a.original_uri, a.content_hash,
                 a.rights_declaration, a.metadata_json(), a.created_at),
            )
            self.conn.commit()
            return a.id
        except sqlite3.IntegrityError:
            self.conn.rollback()
            row = self.conn.execute(
                "SELECT id FROM source_assets WHERE project_id=? AND content_hash=?",
                (a.project_id, a.content_hash),
            ).fetchone()
            return str(row["id"]) if row is not None else a.id

    def get(self, asset_id: str) -> Optional[SourceAsset]:
        row = self.conn.execute("SELECT * FROM source_assets WHERE id=?", (asset_id,)).fetchone()
        return _row_to_source_asset(row) if row is not None else None


class JobRepo:
    """``jobs`` 表（任务引擎底层）。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(self, j: Job) -> str:
        cols = (
            "id,job_type,state,stage,payload,progress,attempt_count,"
            "max_attempts,lease_owner,lease_expires_at,heartbeat_at,"
            "error_code,result_artifact_ids,created_at,updated_at"
        )
        self.conn.execute(
            f"INSERT INTO jobs ({cols}) VALUES ({_q(15)})",
            (j.id, j.job_type, j.state.value, j.stage.value if j.stage else None, j.payload_json(),
             j.progress, j.attempt_count, j.max_attempts, j.lease_owner, j.lease_expires_at,
             j.heartbeat_at, j.error_code, j.result_json(), j.created_at, j.updated_at),
        )
        self.conn.commit()
        return j.id

    def get(self, job_id: str) -> Optional[Job]:
        row = self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return _row_to_job(row) if row is not None else None

    def update_heartbeat(self, job_id: str, ts: str) -> None:
        self.conn.execute(
            "UPDATE jobs SET heartbeat_at=?, updated_at=? WHERE id=?", (ts, ts, job_id)
        )
        self.conn.commit()

    def update_state(
        self,
        job_id: str,
        to_state: JobState,
        progress: Optional[float] = None,
        error: Optional[str] = None,
        stage: Optional[JobStage] = None,
    ) -> Job:
        now = datetime.now(UTC).isoformat()
        self.conn.execute(
            "UPDATE jobs SET state=?, progress=COALESCE(?,progress), "
            "error_code=?, stage=COALESCE(?,stage), updated_at=? WHERE id=?",
            (to_state.value, progress, error, stage.value if stage else None, now, job_id),
        )
        self.conn.commit()
        got = self.get(job_id)
        assert got is not None
        return got


class ContentVersionRepo:
    """``content_versions`` 表。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, cv: ContentVersion) -> str:
        cols = (
            "id,project_id,parent_version_id,content_type,"
            "content,content_hash,producer,created_at"
        )
        self.conn.execute(
            f"INSERT INTO content_versions ({cols}) VALUES ({_q(8)})",
            (cv.id, cv.project_id, cv.parent_version_id, cv.content_type, cv.content,
             cv.content_hash, cv.producer_json(), cv.created_at),
        )
        self.conn.commit()
        return cv.id


class Repos:
    """聚合所有 repo，统一从同一 ``conn`` 构造。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.workspaces: WorkspaceRepo = WorkspaceRepo(conn)
        self.projects: ProjectRepo = ProjectRepo(conn)
        self.source_assets: SourceAssetRepo = SourceAssetRepo(conn)
        self.jobs: JobRepo = JobRepo(conn)
        self.content_versions: ContentVersionRepo = ContentVersionRepo(conn)
