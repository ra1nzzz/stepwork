/** 工作区 Tab：新建、重命名与归档。 */
import { useCallback, useEffect, useState } from "react";
import {
  buildEnvelope,
  dispatchCommand,
  getWorkspaceId,
  setWorkspaceId,
} from "@/lib/tauri";
import type { WorkspaceRow } from "@/lib/types";

export function WorkspacesPanel() {
  const [rows, setRows] = useState<WorkspaceRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  // PRD-WS-001：当前工作区（持久化在本地，切换后全局信封随之改变）
  const [currentWs, setCurrentWs] = useState(getWorkspaceId());

  function handleSwitch(workspaceId: string) {
    setWorkspaceId(workspaceId);
    setCurrentWs(workspaceId);
    // 切换工作区等于换了整个数据上下文，直接重载以清空各 store 的旧数据
    globalThis.location?.reload();
  }

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const env = buildEnvelope("ListWorkspaces", getWorkspaceId(), null, {});
      const res = await dispatchCommand(env);
      if (!res.ok) throw new Error(res.error ?? "LIST_WORKSPACES_FAILED");
      const detail = (res.detail ?? {}) as { workspaces?: WorkspaceRow[] };
      setRows(detail.workspaces ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function handleCreate() {
    const name = newName.trim();
    if (!name) return;
    void run(async () => {
      const env = buildEnvelope("CreateWorkspace", getWorkspaceId(), null, { name });
      const res = await dispatchCommand(env);
      if (!res.ok) throw new Error(res.error ?? "CREATE_WORKSPACE_FAILED");
      setNewName("");
    });
  }

  function handleRename(workspaceId: string) {
    const name = renameValue.trim();
    if (!name) return;
    void run(async () => {
      const env = buildEnvelope("RenameWorkspace", getWorkspaceId(), null, {
        workspaceId,
        name,
      });
      const res = await dispatchCommand(env);
      if (!res.ok) throw new Error(res.error ?? "RENAME_WORKSPACE_FAILED");
      setRenamingId(null);
    });
  }

  function handleArchive(workspaceId: string) {
    void run(async () => {
      const env = buildEnvelope("ArchiveWorkspace", getWorkspaceId(), null, {
        workspaceId,
      });
      const res = await dispatchCommand(env);
      if (!res.ok) throw new Error(res.error ?? "ARCHIVE_WORKSPACE_FAILED");
    });
  }

  return (
    <>
      {error && (
        <p className="error-text" style={{ color: "var(--danger)", marginBottom: 12 }}>
          {error}
        </p>
      )}

      <p className="form-help">
        当前工作区：<span className="mono">{currentWs}</span>
        （切换后新建的项目、任务与配置都归属该工作区）
      </p>

      {loading && <p className="panel-meta">加载工作区…</p>}
      {!loading && rows.length === 0 && (
        <p className="panel-meta">尚无工作区记录（已归档的工作区不在列表中）。</p>
      )}
      {rows.length > 0 && (
        <div className="task-list stack-md">
          {rows.map((w) => (
            <div className="task-item" key={w.id}>
              <div className="task-top">
                <div>
                  {renamingId === w.id ? (
                    <div className="inline-actions">
                      <input
                        className="field"
                        value={renameValue}
                        onChange={(e) => setRenameValue(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") handleRename(w.id);
                        }}
                        disabled={busy}
                        aria-label="工作区新名称"
                      />
                      <button
                        className="btn small primary"
                        type="button"
                        onClick={() => handleRename(w.id)}
                        disabled={busy || !renameValue.trim()}
                      >
                        保存
                      </button>
                      <button
                        className="btn small ghost"
                        type="button"
                        onClick={() => setRenamingId(null)}
                        disabled={busy}
                      >
                        取消
                      </button>
                    </div>
                  ) : (
                    <>
                      <div className="task-name">{w.name}</div>
                      <div className="row-sub mono">{w.id}</div>
                    </>
                  )}
                </div>
                {renamingId !== w.id && (
                  <div className="task-actions">
                    <button
                      className="btn small ghost"
                      type="button"
                      onClick={() => {
                        setRenamingId(w.id);
                        setRenameValue(w.name);
                      }}
                      disabled={busy}
                    >
                      重命名
                    </button>
                    {/* PRD-WS-001：切换当前工作区（此前 getWorkspaceId 恒返回
                        默认值，新建的工作区无法成为当前上下文） */}
                    <button
                      className={`btn small ${currentWs === w.id ? "" : "primary"}`}
                      type="button"
                      onClick={() => handleSwitch(w.id)}
                      disabled={busy || currentWs === w.id}
                    >
                      {currentWs === w.id ? "当前工作区" : "切换到此工作区"}
                    </button>
                    <button
                      className="btn small ghost"
                      type="button"
                      onClick={() => handleArchive(w.id)}
                      disabled={busy || currentWs === w.id}
                      title={
                        currentWs === w.id ? "不能归档当前工作区" : undefined
                      }
                    >
                      归档
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="form-row">
        <label htmlFor="newWorkspaceName">新建工作区</label>
        <div className="inline-actions">
          <input
            id="newWorkspaceName"
            className="field"
            value={newName}
            placeholder="工作区名称"
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleCreate();
            }}
            disabled={busy}
          />
          <button
            className="btn small primary"
            type="button"
            onClick={handleCreate}
            disabled={busy || !newName.trim()}
          >
            新建
          </button>
        </div>
      </div>
      <div className="inline-actions gap-top-md">
        <button
          className="btn small ghost"
          type="button"
          onClick={() => void load()}
          disabled={loading || busy}
        >
          刷新
        </button>
      </div>
    </>
  );
}

/* ---------- AI Provider ---------- */
