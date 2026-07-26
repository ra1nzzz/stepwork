/**
 * 项目列表页 — 真实接入后端
 *
 * 接通命令：
 *   - ListProjects：进入页面时加载，刷新按钮触发重载
 *   - ExportProject：导出选中项目为 zip
 *   - ImportProject：选择 zip 文件后导入
 *
 * 由于后端无 ArchiveProject / UpdateProject，归档/恢复 UI 已移除。
 */

import { useEffect, useRef, useState } from "react";
import { buildEnvelope, dispatchCommand, isTauri } from "@/lib/tauri";
import { useViewStore } from "@/stores/useViewStore";

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
  const [creating, setCreating] = useState(false);
  const importInputRef = useRef<HTMLInputElement | null>(null);
  const inTauri = isTauri();

  // 按标题本地过滤
  const filteredRows = searchKeyword.trim()
    ? rows.filter((r) =>
        r.title.toLowerCase().includes(searchKeyword.trim().toLowerCase()),
      )
    : rows;

  async function loadProjects() {
    setIsLoading(true);
    setError(null);
    try {
      const env = buildEnvelope("ListProjects", "ws-local", null, {});
      const res = await dispatchCommand(env);
      if (!res.ok) throw new Error(res.error ?? "LIST_PROJECTS_FAILED");
      const detail = (res.detail ?? {}) as { projects?: ProjectRow[] };
      setRows(detail.projects ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    void loadProjects();
  }, []);

  async function handleExport() {
    if (!selectedId) return;
    setExporting(true);
    setNotice(null);
    try {
      const env = buildEnvelope("ExportProject", "ws-local", null, {
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

  async function handleImport(file: File) {
    setImporting(true);
    setNotice(null);
    try {
      const bundlePath = inTauri
        ? ((file as File & { path?: string }).path ?? file.name)
        : file.name;
      const env = buildEnvelope("ImportProject", "ws-local", null, {
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

  function continueProject() {
    if (selectedId) {
      setSelectedProjectId(selectedId);
    }
    setCreateSubView("import");
    setView("create");
  }

  async function newProject() {
    setCreating(true);
    setError(null);
    try {
      const env = buildEnvelope("CreateProject", "ws-local", null, {
        title: `未命名项目 ${new Date().toLocaleString("zh-CN")}`,
      });
      const res = await dispatchCommand(env);
      if (!res.ok) throw new Error(res.error ?? "CREATE_PROJECT_FAILED");
      const detail = (res.detail ?? {}) as { project?: { id?: string } };
      const newId = detail.project?.id;
      if (!newId) throw new Error("CREATE_PROJECT_NO_ID");
      setSelectedProjectId(newId);
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
              onClick={() => importInputRef.current?.click()}
              disabled={importing}
            >
              {importing ? "导入中…" : "导入项目"}
            </button>
            <input
              ref={importInputRef}
              type="file"
              accept=".zip"
              style={{ display: "none" }}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void handleImport(f);
                e.target.value = "";
              }}
            />
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
                <th>创建时间</th>
                <th>最近更新</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody id="projectRows">
              {isLoading && (
                <tr>
                  <td colSpan={5} style={{ textAlign: "center", padding: 24 }}>
                    正在加载项目列表…
                  </td>
                </tr>
              )}
              {!isLoading && filteredRows.length === 0 && (
                <tr>
                  <td colSpan={5} style={{ textAlign: "center", padding: 24, color: "var(--muted)" }}>
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
                  <td className="mono">{formatDate(row.created_at)}</td>
                  <td className="mono">{formatDate(row.updated_at)}</td>
                  <td>
                    <button
                      className="btn small"
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        continueProject();
                      }}
                    >
                      继续
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <div className="inline-actions" style={{ marginTop: 18 }}>
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
