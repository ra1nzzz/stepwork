/**
 * 项目列表页 — 真实接入后端
 *
 * 接通命令：
 *   - ListProjects：进入页面时加载，刷新按钮触发重载
 *   - ExportProject：导出选中项目为 zip
 *   - ImportProject：选择 zip 文件后导入
 *   - Tranche 2：ListBrandProfiles + SetProjectBrandProfile
 *     （项目 ↔ 品牌档案关联，生成注入用）
 *
 * 由于后端无 ArchiveProject / UpdateProject，归档/恢复 UI 已移除。
 */

import { useEffect, useState } from "react";
import { buildEnvelope, dispatchCommand, getWorkspaceId, isTauri } from "@/lib/tauri";
import { useViewStore } from "@/stores/useViewStore";
import type { BrandProfile, SetProjectBrandProfilePayload } from "@/lib/types";

interface ProjectRow {
  id: string;
  workspace_id: string;
  title: string;
  status: string;
  brand_profile_id: string | null;
  current_content_version_id: string | null;
  created_at: string;
  updated_at: string;
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function ProjectsView() {
  const setView = useViewStore((s) => s.setView);
  const openProjectDetail = useViewStore((s) => s.openProjectDetail);
  const setCreateSubView = useViewStore((s) => s.setCreateSubView);
  const setSelectedProjectId = useViewStore((s) => s.setSelectedProjectId);

  const [rows, setRows] = useState<ProjectRow[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [importing, setImporting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [searchKeyword, setSearchKeyword] = useState("");
  // PRD-WS-003：标签筛选与排序（服务端执行）
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [availableTags, setAvailableTags] = useState<string[]>([]);
  const [sortMode, setSortMode] = useState<"recent" | "created" | "title">(
    "recent",
  );
  const [creating, setCreating] = useState(false);
  // Tranche 2：品牌档案关联（生成注入用）
  const [brandProfiles, setBrandProfiles] = useState<BrandProfile[]>([]);
  const [bindingId, setBindingId] = useState<string | null>(null);
  const inTauri = isTauri();

  // PRD-WS-003：搜索/标签/排序改为**后端**执行（此前只是对已拉取列表做
  // 本地 includes，数据一多就不准，也无法按标签或最近访问筛）
  const filteredRows = rows;

  async function loadProjects() {
    setIsLoading(true);
    setError(null);
    try {
      const listPayload: Record<string, unknown> = {};
      if (searchKeyword.trim()) listPayload.keyword = searchKeyword.trim();
      if (selectedTags.length > 0) listPayload.tags = selectedTags;
      listPayload.sort = sortMode;
      const env = buildEnvelope(
        "ListProjects",
        getWorkspaceId(),
        null,
        listPayload,
      );
      const res = await dispatchCommand(env);
      if (!res.ok) throw new Error(res.error ?? "LIST_PROJECTS_FAILED");
      const detail = (res.detail ?? {}) as {
        projects?: ProjectRow[];
        available_tags?: string[];
      };
      setRows(detail.projects ?? []);
      setAvailableTags(detail.available_tags ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setIsLoading(false);
    }
  }

  // 筛选条件变化 → 重新向后端查询（关键词防抖 300ms）
  useEffect(() => {
    const timer = window.setTimeout(() => void loadProjects(), 300);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchKeyword, selectedTags, sortMode]);

  useEffect(() => {
    void loadProjects();
    // 品牌档案列表（关联 select 的选项；后端未连接时静默保持空）
    void (async () => {
      try {
        const env = buildEnvelope("ListBrandProfiles", getWorkspaceId(), null, {});
        const res = await dispatchCommand(env);
        if (!res.ok) return;
        const detail = (res.detail ?? {}) as { profiles?: BrandProfile[] };
        setBrandProfiles(detail.profiles ?? []);
      } catch {
        /* 静默 */
      }
    })();
  }, []);

  /** 项目 ↔ 品牌档案关联（SetProjectBrandProfile；空值解除关联） */
  async function handleBindBrand(projectId: string, profileId: string) {
    setBindingId(projectId);
    setError(null);
    try {
      const payload: SetProjectBrandProfilePayload = {
        projectId,
        profileId: profileId || null,
      };
      const env = buildEnvelope("SetProjectBrandProfile", getWorkspaceId(), projectId, payload);
      const res = await dispatchCommand(env);
      if (!res.ok) throw new Error(res.error ?? "SET_BRAND_PROFILE_FAILED");
      setRows((prev) =>
        prev.map((r) =>
          r.id === projectId ? { ...r, brand_profile_id: profileId || null } : r,
        ),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBindingId(null);
    }
  }

  async function handleExport() {
    if (!selectedId) return;
    setExporting(true);
    setNotice(null);
    try {
      const env = buildEnvelope("ExportProject", getWorkspaceId(), null, {
        projectId: selectedId,
      });
      const res = await dispatchCommand(env);
      if (!res.ok) throw new Error(res.error ?? "EXPORT_FAILED");
      const detail = (res.detail ?? {}) as { bundle_path?: string };
      setNotice(`已导出到：${detail.bundle_path ?? "未知路径"}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setExporting(false);
    }
  }

  /** 导入项目包：dialog 选择 zip 的真实绝对路径（浏览器环境无路径可用，按钮禁用） */
  async function handleImport() {
    if (!inTauri || importing) return;
    setImporting(true);
    setNotice(null);
    try {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const bundlePath = await open({
        multiple: false,
        filters: [{ name: "项目包", extensions: ["zip"] }],
      });
      if (!bundlePath || typeof bundlePath !== "string") return;
      const env = buildEnvelope("ImportProject", getWorkspaceId(), null, {
        bundlePath,
      });
      const res = await dispatchCommand(env);
      if (!res.ok) throw new Error(res.error ?? "IMPORT_FAILED");
      const detail = (res.detail ?? {}) as { project_id?: string };
      setNotice(`已导入项目：${detail.project_id ?? "未知"}`);
      await loadProjects();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setImporting(false);
    }
  }

  /** 进入创作流程前必须写入 selectedProjectId（下游 store 读取作为 envelope.projectId） */
  function continueProject(row: ProjectRow) {
    setSelectedProjectId(row.id, row.title);
    setCreateSubView("import");
    setView("create");
  }

  async function newProject() {
    setCreating(true);
    setError(null);
    try {
      const title = `未命名项目 ${new Date().toLocaleString("zh-CN")}`;
      const env = buildEnvelope("CreateProject", getWorkspaceId(), null, { title });
      const res = await dispatchCommand(env);
      if (!res.ok) throw new Error(res.error ?? "CREATE_PROJECT_FAILED");
      const detail = (res.detail ?? {}) as { project?: { id?: string; title?: string } };
      const newId = detail.project?.id;
      if (!newId) throw new Error("CREATE_PROJECT_NO_ID");
      setSelectedProjectId(newId, detail.project?.title ?? title);
      setCreateSubView("import");
      setView("create");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  }

  return (
    <>
      <section className="page-head" data-od-id="projects-heading">
        <div>
          <p className="eyebrow">PROJECT LIBRARY</p>
          <h1>项目与内容版本</h1>
          <p className="page-subtitle">
            搜索、导出和导入项目；点击「继续」进入创作流程。
          </p>
        </div>
        <span className={`status ${isLoading ? "ai" : error ? "danger" : "success"}`}>
          {isLoading ? "加载中" : error ? "出错" : `${rows.length} 个项目`}
        </span>
      </section>

      <section className="panel">
        <div className="panel-head">
          {/* PRD-WS-003：标签筛选（点选切换，多选为 AND 语义） */}
          {availableTags.length > 0 && (
            <div className="inline-actions" data-od-id="project-tag-filter">
              <span className="panel-meta self-center">
                标签：
              </span>
              {availableTags.map((tag) => (
                <button
                  key={tag}
                  type="button"
                  className={`btn small ${
                    selectedTags.includes(tag) ? "primary" : "ghost"
                  }`}
                  onClick={() =>
                    setSelectedTags((prev) =>
                      prev.includes(tag)
                        ? prev.filter((t) => t !== tag)
                        : [...prev, tag],
                    )
                  }
                  disabled={isLoading}
                >
                  {tag}
                </button>
              ))}
              {selectedTags.length > 0 && (
                <button
                  type="button"
                  className="btn small ghost"
                  onClick={() => setSelectedTags([])}
                >
                  清除筛选
                </button>
              )}
            </div>
          )}

          <div className="searchbar">
            <input
              className="field"
              id="projectSearch"
              type="search"
              placeholder="搜索项目（标题）"
              value={searchKeyword}
              onChange={(e) => setSearchKeyword(e.target.value)}
              disabled={isLoading}
            />
          </div>
          <div className="filters">
            {/* PRD-WS-003：排序（最近访问 / 创建时间 / 标题） */}
            <select
              className="select"
              value={sortMode}
              onChange={(e) =>
                setSortMode(e.target.value as "recent" | "created" | "title")
              }
              disabled={isLoading}
              aria-label="项目排序"
            >
              <option value="recent">最近访问</option>
              <option value="created">创建时间</option>
              <option value="title">标题</option>
            </select>
            <button
              className="btn small ghost"
              type="button"
              onClick={() => void loadProjects()}
              disabled={isLoading}
            >
              {isLoading ? "加载中…" : "刷新"}
            </button>
            <button
              className="btn small ghost"
              type="button"
              id="exportProject"
              onClick={() => void handleExport()}
              disabled={!selectedId || exporting}
            >
              {exporting ? "导出中…" : "导出选中项目"}
            </button>
            <button
              className="btn small"
              type="button"
              id="importProject"
              onClick={() => void handleImport()}
              disabled={!inTauri || importing}
              title={inTauri ? undefined : "导入项目需要在桌面应用中使用"}
            >
              {importing ? "导入中…" : "导入项目"}
            </button>
          </div>
        </div>

        {error && (
          <p className="error-text" style={{ color: "var(--danger)", padding: "12px 20px 0" }}>
            {error}
          </p>
        )}

        {notice && (
          <p className="panel-meta" style={{ padding: "12px 20px 0", color: "var(--success, #16a34a)" }}>
            {notice}
          </p>
        )}

        <div className="table-wrap">
          <table className="product-table">
            <thead>
              <tr>
                <th>项目</th>
                <th>状态</th>
                <th>品牌档案</th>
                <th>创建时间</th>
                <th>最近更新</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody id="projectRows">
              {isLoading && (
                <tr>
                  <td colSpan={6} style={{ textAlign: "center", padding: 24 }}>
                    正在加载项目列表…
                  </td>
                </tr>
              )}
              {!isLoading && filteredRows.length === 0 && (
                <tr>
                  <td colSpan={6} style={{ textAlign: "center", padding: 24, color: "var(--muted)" }}>
                    {searchKeyword ? "无匹配项目。" : "尚无项目。点击「新建项目」开始第一个创作。"}
                  </td>
                </tr>
              )}
              {filteredRows.map((row) => (
                <tr
                  key={row.id}
                  data-state={row.status}
                  onClick={() => setSelectedId(row.id)}
                  style={{
                    cursor: "pointer",
                    background: selectedId === row.id ? "var(--hover-bg, rgba(0,0,0,0.04))" : undefined,
                  }}
                >
                  <td>
                    <strong>{row.title}</strong>
                    <div className="row-sub mono">{row.id.slice(0, 16)}…</div>
                  </td>
                  <td>
                    <span className="status">{row.status}</span>
                  </td>
                  <td onClick={(e) => e.stopPropagation()}>
                    <select
                      className="select"
                      aria-label={`项目 ${row.title} 的品牌档案`}
                      value={row.brand_profile_id ?? ""}
                      disabled={bindingId === row.id}
                      onChange={(e) => void handleBindBrand(row.id, e.target.value)}
                    >
                      <option value="">未关联</option>
                      {brandProfiles.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="mono">{formatDate(row.created_at)}</td>
                  <td className="mono">{formatDate(row.updated_at)}</td>
                  <td>
                    <button
                      className="btn small"
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        continueProject(row);
                      }}
                    >
                      继续
                    </button>
                    {/* PRD §7：进入项目详情（概览/素材/版本/变体/溯源页签） */}
                    <button
                      className="btn small ghost"
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        setSelectedProjectId(row.id, row.title);
                        openProjectDetail(row.id);
                      }}
                    >
                      详情
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <div className="inline-actions gap-top-lg">
        <button
          type="button"
          className="btn primary"
          onClick={() => void newProject()}
          disabled={creating}
        >
          {creating ? "创建中…" : "新建项目"}
        </button>
      </div>
    </>
  );
}
