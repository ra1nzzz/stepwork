/** 数据与存储 Tab：保留策略与清理时机（PRD-SRC-005）。 */
import { useState } from "react";
import { buildEnvelope, dispatchCommand, getWorkspaceId } from "@/lib/tauri";
import type { CleanupMode, SettingsConfig } from "@/stores/useSettingsStore";

export function DataPanel({
  settings,
  update,
  busy,
  onCheck,
}: {
  settings: SettingsConfig;
  update: (patch: Partial<SettingsConfig>) => void;
  busy: boolean;
  onCheck: () => void;
}) {
  const d = settings.data;
  // PRD-SRC-005：手动触发清理（cleanupMode=manual 时这是唯一入口）
  const [cleanupBusy, setCleanupBusy] = useState(false);
  const [cleanupNotice, setCleanupNotice] = useState<string | null>(null);

  async function runCleanup() {
    setCleanupBusy(true);
    setCleanupNotice(null);
    try {
      const env = buildEnvelope("RunCleanup", getWorkspaceId(), null, {
        mode: "immediate",
      });
      const res = await dispatchCommand(env);
      if (!res.ok) {
        setCleanupNotice(res.error ?? "清理失败");
        return;
      }
      const removed = (res.detail as { removed?: number } | null)?.removed ?? 0;
      setCleanupNotice(`已清理 ${removed} 个临时文件`);
    } catch (e) {
      setCleanupNotice(e instanceof Error ? e.message : String(e));
    } finally {
      setCleanupBusy(false);
    }
  }

  return (
    <>
      <div className="form-row">
        <label htmlFor="workspaceDefaultPath">项目默认文件夹</label>
        <div>
          <input
            id="workspaceDefaultPath"
            className="field w-full" 
            value={settings.workspace.defaultPath}
            placeholder="~/STEPWORK (STEPWORK_HOME)"
            onChange={(e) => update({ workspace: { ...settings.workspace, defaultPath: e.target.value } })}
          />
          <p className="form-help">本地项目空间 / STEPWORK_HOME</p>
        </div>
      </div>
      <div className="form-row">
        <label htmlFor="retentionDays">保留天数</label>
        <input
          id="retentionDays"
          className="field"
          type="number"
          min="1"
          value={d.retentionDays}
          onChange={(e) => update({ data: { ...d, retentionDays: Number(e.target.value) } })}
        />
      </div>
      {/* PRD-SRC-005：清理时机三选一 + 手动触发入口 */}
      <div className="form-row">
        <label htmlFor="cleanupMode">临时文件清理</label>
        <select
          id="cleanupMode"
          className="field"
          value={d.cleanupMode}
          onChange={(e) =>
            update({
              data: { ...d, cleanupMode: e.target.value as CleanupMode },
            })
          }
        >
          <option value="immediate">立即（导入完成后即清）</option>
          <option value="scheduled">定时（启动时按保留天数清）</option>
          <option value="manual">手动（仅在点击时清）</option>
        </select>
      </div>
      <div className="form-row">
        <span className="row-label">立即清理</span>
        <div className="inline-actions">
          <button
            type="button"
            className="btn small ghost"
            onClick={() => void runCleanup()}
            disabled={cleanupBusy}
          >
            {cleanupBusy ? "清理中…" : "立即清理临时文件"}
          </button>
          {cleanupNotice && (
            <span className="panel-meta self-center" role="status">
              {cleanupNotice}
            </span>
          )}
        </div>
      </div>
      <div className="form-row">
        <span className="row-label">策略</span>
        <div className="check-grid">
          <label>
            <input
              type="checkbox"
              checked={d.desensitize}
              onChange={(e) => update({ data: { ...d, desensitize: e.target.checked } })}
            />
            诊断包脱敏
          </label>
          <label>
            <input
              type="checkbox"
              checked={d.projectDelete}
              onChange={(e) => update({ data: { ...d, projectDelete: e.target.checked } })}
            />
            允许项目级删除
          </label>
        </div>
      </div>
      <div className="form-row">
        <label htmlFor="uploadScope">上传范围</label>
        <input
          id="uploadScope"
          className="field w-full" 
          value={d.uploadScope}
          onChange={(e) => update({ data: { ...d, uploadScope: e.target.value } })}
        />
      </div>
      <div className="empty">
        <h2>数据与存储</h2>
        <p>素材与 Artifact 默认存储在本地项目空间。已提供项目级删除、30 天任务日志保留和诊断包脱敏策略。</p>
        <button type="button" className="btn" onClick={onCheck} disabled={busy} aria-busy={busy}>
          检查当前配置
        </button>
      </div>
    </>
  );
}

/* ---------- 导入与导出 ---------- */
