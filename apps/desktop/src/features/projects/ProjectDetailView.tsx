/**
 * 项目详情（PRD §7 信息架构：项目内页签）。
 *
 * PRD 要求项目内部有 Overview / Sources / Analysis / Script / Render /
 * Platforms / Provenance 等页签；此前项目页是**扁平表格**，没有详情页也
 * 没有页签，项目上下文全靠 useViewStore.selectedProjectId 一个全局值贯穿。
 *
 * 这里补上详情容器：概览直接呈现项目事实（素材数 / 版本数 / 标签 /
 * 最近访问），其余页签复用既有创作/发布/溯源视图，避免重复实现。
 */

import { useCallback, useEffect, useState } from "react";
import { buildEnvelope, dispatchCommand, getWorkspaceId } from "@/lib/tauri";
import { useViewStore } from "@/stores/useViewStore";
import { ProvenanceView } from "@/features/provenance/ProvenanceView";
import type { ContentVersionSummary } from "@/lib/types";

type DetailTab =
  | "overview"
  | "sources"
  | "versions"
  | "platforms"
  | "provenance";

const TABS: { id: DetailTab; label: string }[] = [
  { id: "overview", label: "概览" },
  { id: "sources", label: "素材" },
  { id: "versions", label: "内容版本" },
  { id: "platforms", label: "平台变体" },
  { id: "provenance", label: "溯源" },
];

interface AssetRow {
  id: string;
  kind: string;
  local_uri: string;
  original_uri: string | null;
  rights_declaration: string | null;
  author: string | null;
  created_at: string;
}

interface VariantRow {
  id: string;
  platform: string;
  title: string;
  created_at: string;
}

interface ProjectDetail {
  id: string;
  title: string;
  status: string;
  tags: string[];
  created_at: string;
  updated_at: string;
  last_accessed_at: string | null;
}

export function ProjectDetailView({ projectId }: { projectId: string }) {
  const setView = useViewStore((s) => s.setView);
  const [tab, setTab] = useState<DetailTab>("overview");
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [assets, setAssets] = useState<AssetRow[]>([]);
  const [versions, setVersions] = useState<ContentVersionSummary[]>([]);
  const [variants, setVariants] = useState<VariantRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const ws = getWorkspaceId();
      // GetProject 同时刷新 last_accessed_at（PRD-WS-003）
      const proj = await dispatchCommand(
        buildEnvelope("GetProject", ws, projectId, { projectId }),
      );
      if (!proj.ok) {
        setError(proj.error ?? "读取项目失败");
        return;
      }
      setProject(
        (proj.detail as { project?: ProjectDetail } | null)?.project ?? null,
      );

      const [assetRes, versionRes, variantRes] = await Promise.all([
        dispatchCommand(
          buildEnvelope("ListSourceAssets", ws, projectId, { projectId }),
        ),
        dispatchCommand(
          buildEnvelope("ListContentVersions", ws, projectId, {
            projectId,
            limit: 50,
          }),
        ),
        dispatchCommand(
          buildEnvelope("ListPlatformVariants", ws, projectId, { projectId }),
        ),
      ]);
      if (assetRes.ok) {
        setAssets(
          ((assetRes.detail ?? {}) as { assets?: AssetRow[] }).assets ?? [],
        );
      }
      if (versionRes.ok) {
        setVersions(
          ((versionRes.detail ?? {}) as { versions?: ContentVersionSummary[] })
            .versions ?? [],
        );
      }
      if (variantRes.ok) {
        setVariants(
          ((variantRes.detail ?? {}) as { variants?: VariantRow[] }).variants ??
            [],
        );
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className="feature-view" data-od-id="project-detail">
      <div className="page-head">
        <div>
          <p className="eyebrow">PROJECT</p>
          <h1>{project?.title ?? "项目详情"}</h1>
          <p className="page-subtitle">
            {project?.tags?.length
              ? `标签：${project.tags.join(" / ")} · `
              : ""}
            {project?.last_accessed_at
              ? `最近访问 ${new Date(project.last_accessed_at).toLocaleString()}`
              : "尚未访问过"}
          </p>
        </div>
        <div className="inline-actions">
          <button
            className="btn small primary"
            type="button"
            onClick={() => setView("create")}
          >
            进入创作
          </button>
          <button
            className="btn small ghost"
            type="button"
            onClick={() => setView("projects")}
          >
            返回列表
          </button>
        </div>
      </div>

      <nav className="tabs" role="tablist" aria-label="项目页签">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`tab${tab === t.id ? " active" : ""}`}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {error && <p className="error-text">{error}</p>}
      {loading && <p className="feature-sub">加载中…</p>}

      {tab === "overview" && project && (
        <section className="layout-main section-gap">
          <article className="panel">
            <div className="panel-body">
              <dl className="provenance">
                <div className="provenance-row">
                  <dt>状态</dt>
                  <dd>{project.status}</dd>
                </div>
                <div className="provenance-row">
                  <dt>素材</dt>
                  <dd>{assets.length} 个</dd>
                </div>
                <div className="provenance-row">
                  <dt>内容版本</dt>
                  <dd>{versions.length} 个</dd>
                </div>
                <div className="provenance-row">
                  <dt>平台变体</dt>
                  <dd>{variants.length} 个</dd>
                </div>
                <div className="provenance-row">
                  <dt>创建于</dt>
                  <dd>{new Date(project.created_at).toLocaleString()}</dd>
                </div>
              </dl>
            </div>
          </article>
        </section>
      )}

      {tab === "sources" && (
        <section className="layout-main section-gap">
          <article className="panel">
            <div className="panel-body task-list">
              {assets.length === 0 ? (
                <p className="panel-meta">尚无素材。到「创作 → 素材分析」导入。</p>
              ) : (
                assets.map((a) => (
                  <div className="task-item" key={a.id}>
                    <div className="task-top">
                      <div>
                        <div className="task-name">{a.local_uri}</div>
                        {/* PRD-SRC-003 可追溯四要素 */}
                        <div className="row-sub">
                          {a.kind}
                          {a.author ? ` · 作者 ${a.author}` : ""}
                          {a.rights_declaration
                            ? ` · 权利 ${a.rights_declaration}`
                            : ""}
                          {` · 导入 ${new Date(a.created_at).toLocaleString()}`}
                        </div>
                        {a.original_uri && (
                          <div className="row-sub mono">来源 {a.original_uri}</div>
                        )}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </article>
        </section>
      )}

      {tab === "versions" && (
        <section className="layout-main section-gap">
          <article className="panel">
            <div className="panel-body task-list">
              {versions.length === 0 ? (
                <p className="panel-meta">尚无内容版本。</p>
              ) : (
                versions.map((v) => (
                  <div className="task-item" key={v.id}>
                    <div className="task-top">
                      <div>
                        <div className="task-name">{v.content_type}</div>
                        <div className="row-sub">
                          {v.id.slice(0, 8)} ·{" "}
                          {new Date(v.created_at).toLocaleString()}
                        </div>
                      </div>
                    </div>
                    {v.preview && (
                      <p className="panel-meta hint-inline">
                        {v.preview}
                      </p>
                    )}
                  </div>
                ))
              )}
            </div>
          </article>
        </section>
      )}

      {tab === "platforms" && (
        <section className="layout-main section-gap">
          <article className="panel">
            <div className="panel-body task-list">
              {variants.length === 0 ? (
                <p className="panel-meta">尚无平台变体。到「发布」页创建。</p>
              ) : (
                variants.map((v) => (
                  <div className="task-item" key={v.id}>
                    <div className="task-top">
                      <div>
                        <div className="task-name">{v.title || "（无标题）"}</div>
                        <div className="row-sub">
                          {v.platform} ·{" "}
                          {new Date(v.created_at).toLocaleString()}
                        </div>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </article>
        </section>
      )}

      {tab === "provenance" && <ProvenanceView />}
    </section>
  );
}
