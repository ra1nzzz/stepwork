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
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Optional

from worker.runtime.hotspot.models import VERDICTS, HotspotItem
from worker.runtime.models import (
    ContentProject,
    ContentVersion,
    Job,
    JobStage,
    JobState,
    SourceAsset,
    VideoScene,
    Workspace,
)


def _q(n: int) -> str:
    """生成 ``n`` 个逗号分隔的 ``?`` 占位符。"""
    return ",".join(["?"] * n)


#: hotspot_items 的排序：同源内保持上游顺序（= 热度顺序），跨源按抓取时间
_HOTSPOT_ORDER = "ORDER BY discovered_at DESC, source, rowid"


def _row_to_hotspot(row: sqlite3.Row) -> HotspotItem:
    raw_meta = row["meta_json"]
    try:
        meta = json.loads(raw_meta) if raw_meta else {}
    except (TypeError, ValueError):
        meta = {}
    return HotspotItem(
        id=str(row["id"]),
        source=str(row["source"]),
        title=str(row["title"]),
        url=str(row["url"] or ""),
        summary=str(row["summary"] or ""),
        published_at=str(row["published_at"]) if row["published_at"] else None,
        score=float(row["score"]) if row["score"] is not None else None,
        meta=meta if isinstance(meta, dict) else {},
        batch_id=str(row["batch_id"] or ""),
        discovered_at=str(row["discovered_at"] or ""),
    )


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

    def update_settings(self, ws_id: str, settings: dict[str, Any]) -> None:
        """覆盖写入某工作区的 ``settings`` 列（JSON）。

        设置页用：非密钥配置落库，跨会话保留；密钥字段由调用方
        预先剥离（见 :mod:`worker.runtime.handlers.config`）。
        """
        self.conn.execute(
            "UPDATE workspaces SET settings=? WHERE id=?",
            (json.dumps(settings), ws_id),
        )
        self.conn.commit()


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

    # 终态集合：迁移进入后清空租约字段（租约只对「执行中」有意义；
    # 残留会让 acquire/sweep 误判、也污染重试链路的可观测性）
    _TERMINAL_STATES: frozenset[JobState] = frozenset(
        {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED, JobState.EXPIRED}
    )

    def update_state(
        self,
        job_id: str,
        to_state: JobState,
        progress: Optional[float] = None,
        error: Optional[str] = None,
        stage: Optional[JobStage] = None,
    ) -> Job:
        now = datetime.now(UTC).isoformat()
        if to_state in self._TERMINAL_STATES:
            # 终态：一并清除 lease_owner / lease_expires_at
            self.conn.execute(
                "UPDATE jobs SET state=?, progress=COALESCE(?,progress), "
                "error_code=?, stage=COALESCE(?,stage), "
                "lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE id=?",
                (to_state.value, progress, error, stage.value if stage else None, now, job_id),
            )
        else:
            # 终态不可回退（WHERE 里带守卫，避免竞态）：取消渲染时主协程已把
            # job 落 CANCELLED，但 ffmpeg 工作线程可能还在跑，其进度回调会
            # 继续提交 RUNNING —— 若不守卫，终态会被改回 RUNNING，任务永久
            # 悬挂。守卫放在 SQL 的 WHERE 而非 Python 判断，避免 check-then-act
            # 之间的窗口。
            placeholders = ",".join(["?"] * len(self._TERMINAL_STATES))
            self.conn.execute(
                "UPDATE jobs SET state=?, progress=COALESCE(?,progress), "
                "error_code=?, stage=COALESCE(?,stage), updated_at=? "
                f"WHERE id=? AND state NOT IN ({placeholders})",
                (
                    to_state.value, progress, error,
                    stage.value if stage else None, now, job_id,
                    *[s.value for s in self._TERMINAL_STATES],
                ),
            )
        self.conn.commit()
        got = self.get(job_id)
        assert got is not None
        return got

    def set_result_artifacts(self, job_id: str, ids: list[str]) -> None:
        """写入任务产出的 artifact id 列表（落库 TEXT）。"""
        self.conn.execute(
            "UPDATE jobs SET result_artifact_ids=?, updated_at=? WHERE id=?",
            (json.dumps(ids), datetime.now(UTC).isoformat(), job_id),
        )
        self.conn.commit()


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

    def get(self, cv_id: str) -> ContentVersion | None:
        row = self.conn.execute(
            "SELECT * FROM content_versions WHERE id=?", (cv_id,)
        ).fetchone()
        return _row_to_content_version(row) if row is not None else None

    def find_by_hash(
        self, project_id: str, content_type: str, content_hash: str
    ) -> str | None:
        """按内容哈希找同项目内的既有版本；找不到返回 ``None``。

        ``content_versions`` 是 append-only 的（每次生成都是一版历史），所以
        INSERT 本身不该去重。但**外部 Agent 会重试**：同一条热点、同一份理由
        重转一次不该产出一份新版本 —— 调用方拿哈希问一句「已经有同一份了吗」，
        比在表上强加唯一约束温和（唯一约束会连带挡住「同输入不同批次」的合法留档）。
        """
        row = self.conn.execute(
            "SELECT id FROM content_versions "
            "WHERE project_id=? AND content_type=? AND content_hash=? "
            "ORDER BY created_at LIMIT 1",
            (project_id, content_type, content_hash),
        ).fetchone()
        return str(row["id"]) if row else None


def _row_to_video_scene(row: sqlite3.Row) -> VideoScene:
    return VideoScene(
        id=str(row["id"]),
        version_id=str(row["version_id"]),
        seq=int(row["seq"]),
        text=str(row["text"] or ""),
        emotion=str(row["emotion"]) if row["emotion"] is not None else None,
        highlight=str(row["highlight"]) if row["highlight"] is not None else None,
        audio_uri=str(row["audio_uri"]) if row["audio_uri"] is not None else None,
        image_uri=str(row["image_uri"]) if row["image_uri"] is not None else None,
        start_sec=float(row["start_sec"] or 0.0),
        duration_sec=float(row["duration_sec"] or 0.0),
        born_at_sec=float(row["born_at_sec"]) if row["born_at_sec"] is not None else None,
        created_at=str(row["created_at"]),
    )


class HotspotRepo:
    """热点事实与反馈（migrations/0014）。

    存在的理由：推荐要**去重**（不知上周推过什么就会反复推同一批）和
    **可学习**（用户说「不感兴趣」之后得记得住）。两件事都要求热点不是
    一次性的调用结果，而是库里的历史。
    """

    _INSERT_COLS = (
        "id,batch_id,workspace_id,source,title,url,summary,"
        "published_at,score,meta_json,discovered_at"
    )

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert_many(
        self, workspace_id: str, items: Sequence[HotspotItem]
    ) -> int:
        """批量落库；``id`` 冲突用 ``OR REPLACE`` 覆盖。

        同 id = 同（源, 标题, 链接）：上一批次抓到过，这次又抓到，是同一条
        事实，覆盖（刷新 discovered_at）比拒绝更符合语义。
        """
        if not items:
            return 0
        self.conn.executemany(
            f"INSERT OR REPLACE INTO hotspot_items ({self._INSERT_COLS}) "
            f"VALUES ({_q(11)})",
            [item.to_row(workspace_id) for item in items],
        )
        self.conn.commit()
        return len(items)

    def list_by_batch(self, batch_id: str) -> list[HotspotItem]:
        rows = self.conn.execute(
            f"SELECT * FROM hotspot_items WHERE batch_id=? {_HOTSPOT_ORDER}",
            (batch_id,),
        ).fetchall()
        return [_row_to_hotspot(r) for r in rows]

    def list_recent(
        self, workspace_id: str, *, limit: int = 200, sources: Sequence[str] | None = None
    ) -> list[HotspotItem]:
        """本工作区最近抓到的热点（跨批次）。"""
        sql = "SELECT * FROM hotspot_items WHERE workspace_id=?"
        params: list[Any] = [workspace_id]
        if sources:
            sql += f" AND source IN ({_q(len(sources))})"
            params.extend(sources)
        sql += f" {_HOTSPOT_ORDER} LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [_row_to_hotspot(r) for r in rows]

    def latest_batch_id(self, workspace_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT batch_id FROM hotspot_items WHERE workspace_id=? "
            "ORDER BY discovered_at DESC LIMIT 1",
            (workspace_id,),
        ).fetchone()
        return str(row["batch_id"]) if row else None

    def get(self, hotspot_id: str, workspace_id: str | None = None) -> HotspotItem | None:
        """按 id 取单条。``workspace_id`` 给定时限定在本工作区内。

        为什么单独给 ``workspace_id`` 而不用「必须传」：推荐结果里带的 id 就是
        全局 id，UI 点「转选题」时手上没有工作区上下文也该能查到；传了的调用方
        则能顺带把「别人工作区的热点」挡在外面。
        """
        if workspace_id is None:
            row = self.conn.execute(
                "SELECT * FROM hotspot_items WHERE id=?", (hotspot_id,)
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM hotspot_items WHERE id=? AND workspace_id=?",
                (hotspot_id, workspace_id),
            ).fetchone()
        return _row_to_hotspot(row) if row is not None else None

    # -------------------------------------------------------------- 反馈

    def record_feedback(
        self,
        *,
        hotspot_id: str,
        workspace_id: str,
        verdict: str,
        project_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        """记一条反馈。**同（热点, 工作区）覆盖而非追加**：一条热点只能有一个
        结论，追加会让学习逻辑无所适从（到底听哪次的）。"""
        if verdict not in VERDICTS:
            raise ValueError(f"unknown verdict: {verdict}")
        stamp = datetime.now(UTC).isoformat()
        self.conn.execute(
            "INSERT INTO hotspot_feedback "
            "(hotspot_id, workspace_id, project_id, verdict, reason, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(hotspot_id, workspace_id) DO UPDATE SET "
            "verdict=excluded.verdict, reason=excluded.reason, "
            "project_id=excluded.project_id, updated_at=excluded.updated_at",
            (hotspot_id, workspace_id, project_id, verdict, reason, stamp, stamp),
        )
        self.conn.commit()

    def feedback_map(self, workspace_id: str) -> dict[str, str]:
        """``{hotspot_id: verdict}``；推荐时整体读入（量级在千级以内）。"""
        rows = self.conn.execute(
            "SELECT hotspot_id, verdict FROM hotspot_feedback WHERE workspace_id=?",
            (workspace_id,),
        ).fetchall()
        return {str(r["hotspot_id"]): str(r["verdict"]) for r in rows}

    def title_feedback_map(self, workspace_id: str) -> dict[str, str]:
        """``{标题: verdict}`` —— 跨批次 url 变化时 id 会变，标题更稳。"""
        rows = self.conn.execute(
            "SELECT f.verdict AS verdict, i.title AS title "
            "FROM hotspot_feedback f JOIN hotspot_items i ON i.id=f.hotspot_id "
            "WHERE f.workspace_id=?",
            (workspace_id,),
        ).fetchall()
        return {str(r["title"]): str(r["verdict"]) for r in rows}


class VideoSceneRepo:
    """``video_scenes`` 表（S2 分幕事实表，migrations/0012）。"""

    #: 允许 ``UpdateVideoScene`` 回填的字段（配音/配图阶段的产出）
    _PRODUCTION_FIELDS: tuple[str, ...] = (
        "audio_uri",
        "image_uri",
        "duration_sec",
        "born_at_sec",
    )

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert_many(self, scenes: list[VideoScene]) -> int:
        """批量插入；同版本同 seq 冲突由 UNIQUE 索引拒绝（不会静默覆盖）。"""
        if not scenes:
            return 0
        cols = (
            "id,version_id,seq,text,emotion,highlight,audio_uri,image_uri,"
            "start_sec,duration_sec,born_at_sec,created_at"
        )
        self.conn.executemany(
            f"INSERT INTO video_scenes ({cols}) VALUES ({_q(12)})",
            [
                (
                    s.id, s.version_id, s.seq, s.text, s.emotion, s.highlight,
                    s.audio_uri, s.image_uri, s.start_sec, s.duration_sec,
                    s.born_at_sec, s.created_at,
                )
                for s in scenes
            ],
        )
        self.conn.commit()
        return len(scenes)

    def delete_by_version(self, version_id: str) -> int:
        cur = self.conn.execute(
            "DELETE FROM video_scenes WHERE version_id=?", (version_id,)
        )
        self.conn.commit()
        return int(cur.rowcount or 0)

    def replace_for_version(
        self, version_id: str, scenes: list[VideoScene]
    ) -> int:
        """**单事务**替换某版本的全部分幕（先删后插）。

        拆成两条语句会有中间态：删完插一半失败，该版本就只剩半截幕，
        而渲染器会照着半截数据出片。所以这里显式包事务。
        """
        with self.conn:
            self.conn.execute(
                "DELETE FROM video_scenes WHERE version_id=?", (version_id,)
            )
            if scenes:
                cols = (
                    "id,version_id,seq,text,emotion,highlight,audio_uri,image_uri,"
                    "start_sec,duration_sec,born_at_sec,created_at"
                )
                self.conn.executemany(
                    f"INSERT INTO video_scenes ({cols}) VALUES ({_q(12)})",
                    [
                        (
                            s.id, s.version_id, s.seq, s.text, s.emotion, s.highlight,
                            s.audio_uri, s.image_uri, s.start_sec, s.duration_sec,
                            s.born_at_sec, s.created_at,
                        )
                        for s in scenes
                    ],
                )
        return len(scenes)

    def list_by_version(self, version_id: str) -> list[VideoScene]:
        rows = self.conn.execute(
            "SELECT * FROM video_scenes WHERE version_id=? ORDER BY seq",
            (version_id,),
        ).fetchall()
        return [_row_to_video_scene(r) for r in rows]

    def get(self, scene_id: str) -> VideoScene | None:
        row = self.conn.execute(
            "SELECT * FROM video_scenes WHERE id=?", (scene_id,)
        ).fetchone()
        return _row_to_video_scene(row) if row is not None else None

    def _apply(
        self, version_id: str, sql: str, rows: list[tuple[Any, ...]]
    ) -> int:
        """单事务批写；``rows`` 每项末尾追加 ``version_id`` 作为守卫条件。"""
        if not rows:
            return 0
        with self.conn:
            self.conn.executemany(sql, [(*r, version_id) for r in rows])
        return len(rows)

    def apply_timeline(self, version_id: str, scenes: list[VideoScene]) -> int:
        """**单事务**回填整条时间轴（``audio_uri`` / ``duration_sec`` /
        ``start_sec``）。

        为什么不逐幕 ``update_production``：时间轴是一致性事实 —— 前幕时长
        变了，后面所有幕的 ``start_sec`` 都得跟着变。逐条写会在中途留下
        「半新半旧」的时间轴，渲染器照着它出片就是字幕与配音错位。

        ``WHERE id=? AND version_id=?``：幕 id 与版本必须同时匹配，防止把
        A 版本的配音挂到 B 版本的幕上。

        Returns:
            更新行数。
        """
        if not scenes:
            return 0
        return self._apply(
            version_id,
            "UPDATE video_scenes SET audio_uri=?, duration_sec=?, start_sec=? "
            "WHERE id=? AND version_id=?",
            [(s.audio_uri, s.duration_sec, s.start_sec, s.id) for s in scenes],
        )

    def apply_born_times(
        self, version_id: str, rows: list[tuple[str, float]]
    ) -> int:
        """**单事务**回填各幕首句出现秒（``born_at_sec``）。

        ``rows`` 为 ``(scene_id, born_sec)``。抽帧目检撞在切句瞬间会取到空
        字幕，这个值让取样点可以前移（S1 遗留项，由渲染步骤实测填入）。

        Returns:
            更新行数。
        """
        with self.conn:
            self.conn.executemany(
                "UPDATE video_scenes SET born_at_sec=? WHERE id=? AND version_id=?",
                [(born, scene_id, version_id) for scene_id, born in rows],
            )
        return len(rows)

    def apply_images(self, version_id: str, scenes: list[VideoScene]) -> int:
        """**单事务**回填各幕配图（``image_uri``）。

        只更新**本次真的生成成功**的幕：失败幕保持原有值（可能是旧图，
        也可能是 NULL），绝不被清成 NULL —— 那是把已有的产出弄丢。

        Returns:
            更新行数。
        """
        return self._apply(
            version_id,
            "UPDATE video_scenes SET image_uri=? WHERE id=? AND version_id=?",
            [(s.image_uri, s.id) for s in scenes],
        )

    def update_production(
        self, scene_id: str, **fields: Any
    ) -> VideoScene | None:
        """回填配音/配图的产出（``audio_uri``/``image_uri``/``duration_sec``/
        ``born_at_sec``）。

        只认白名单字段：文案类字段（``text``/``seq``）不允许从这条路径改，
        否则「改一句要重渲全片」的问题会从另一个口子溜回来。
        """
        updates = {k: v for k, v in fields.items() if k in self._PRODUCTION_FIELDS}
        if not updates:
            return self.get(scene_id)
        assignments = ", ".join(f"{k}=?" for k in updates)
        with self.conn:
            self.conn.execute(
                f"UPDATE video_scenes SET {assignments} WHERE id=?",  # noqa: S608
                (*updates.values(), scene_id),
            )
        return self.get(scene_id)


class Repos:
    """聚合所有 repo，统一从同一 ``conn`` 构造。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.workspaces: WorkspaceRepo = WorkspaceRepo(conn)
        self.projects: ProjectRepo = ProjectRepo(conn)
        self.source_assets: SourceAssetRepo = SourceAssetRepo(conn)
        self.jobs: JobRepo = JobRepo(conn)
        self.content_versions: ContentVersionRepo = ContentVersionRepo(conn)
        self.video_scenes: VideoSceneRepo = VideoSceneRepo(conn)
        self.hotspots: HotspotRepo = HotspotRepo(conn)
