/**
 * 溯源视图（W8）
 * 查询 Artifact / 版本 / 脚本的来源链与变更记录（GetProvenance）。
 * MVP 阶段无真实数据，展示空态 + 说明文案。
 */

import { useState } from "react";
import { buildEnvelope, dispatchCommand, getWorkspaceId } from "@/lib/tauri";
import type { CommandResult } from "@/lib/types";

type SubjectType = "artifact" | "version" | "script";

const SUBJECT_TYPES: { value: SubjectType; label: string }[] = [
  { value: "artifact", label: "素材 (Artifact)" },
  { value: "version", label: "版本 (Version)" },
  { value: "script", label: "脚本 (Script)" },
];

interface ProvenanceRecord {
  version_id: string;
  producer_kind: string;
  produced_at: string;
  parent_version_id: string | null;
  operation: string;
}

export function ProvenanceView() {
  const [subjectType, setSubjectType] = useState<SubjectType>("artifact");
  const [subjectId, setSubjectId] = useState("");
  const [records, setRecords] = useState<ProvenanceRecord[]>([]);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [queried, setQueried] = useState(false);

  async function query() {
    const id = subjectId.trim();
    if (!id) {
      setError("请输入 Subject ID");
      return;
    }
    setIsBusy(true);
    setError(null);
    try {
      const env = buildEnvelope("GetProvenance", getWorkspaceId(), null, {
        subject_type: subjectType,
        subject_id: id,
      });
      const res: CommandResult = await dispatchCommand(env);
      if (!res.ok) {
        setError(res.error ?? "查询失败");
        setRecords([]);
      } else {
        const detail = (res.detail ?? {}) as { records?: ProvenanceRecord[] };
        setRecords(detail.records ?? []);
      }
      setQueried(true);
    } catch (e) {
      setError(String(e));
      setRecords([]);
      setQueried(true);
    } finally {
      setIsBusy(false);
    }
  }

  const isEmpty = queried && records.length === 0 && !error;

  return (
    <section className="feature-view" data-od-id="provenance-view">
      <header className="feature-head">
        <h1>溯源</h1>
        <p className="feature-sub">查询 Artifact / 版本 / 脚本的来源链与变更记录</p>
      </header>

      <div className="provenance-form">
        <label>
          类型
          <select
            value={subjectType}
            onChange={(e) => setSubjectType(e.target.value as SubjectType)}
          >
            {SUBJECT_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Subject ID
          <input
            value={subjectId}
            onChange={(e) => setSubjectId(e.target.value)}
            placeholder="cv-... / asset-..."
            onKeyDown={(e) => {
              if (e.key === "Enter") void query();
            }}
          />
        </label>
        <button
          type="button"
          className="btn primary"
          disabled={isBusy}
          onClick={() => void query()}
        >
          {isBusy ? "查询中…" : "查询"}
        </button>
      </div>

      {error && (
        <p className="error-text" data-od-id="provenance-error">
          {error}
        </p>
      )}

      {isEmpty && (
        <div className="empty-state" data-od-id="provenance-empty">
          <p className="empty-title">暂无溯源记录。</p>
          <p className="empty-sub">
            Provenance 记录将在内容分析/脚本生成时自动写入（W9 启用完整写入路径）。
          </p>
        </div>
      )}

      {records.length > 0 && (
        <ul className="provenance-list">
          {records.map((r, i) => (
            <li key={r.version_id ?? i} className="provenance-item">
              <div className="record-head">
                <span className="status-badge" data-status="succeeded">
                  {r.operation}
                </span>
                <span className="record-producer">{r.producer_kind}</span>
              </div>
              <p className="record-meta">
                version {r.version_id.slice(0, 8)}
                {r.parent_version_id
                  ? ` ← parent ${r.parent_version_id.slice(0, 8)}`
                  : " · 根版本"}
                {r.produced_at ? ` · ${r.produced_at}` : ""}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
