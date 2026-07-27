/** 导入与导出 Tab：项目包完整性校验。 */
import type { SettingsConfig } from "@/stores/useSettingsStore";

export function ExportPanel({
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
  const ex = settings.export;
  return (
    <>
      <div className="form-row">
        <label htmlFor="exportFormat">导出格式</label>
        <select
          id="exportFormat"
          className="select"
          value={ex.format}
          onChange={(e) => update({ export: { ...ex, format: e.target.value as SettingsConfig["export"]["format"] } })}
        >
          <option value="MP4">MP4</option>
          <option value="SRT">SRT</option>
          <option value="WAV">WAV</option>
        </select>
      </div>
      <div className="form-row">
        <span className="row-label">校验</span>
        <div className="check-grid">
          <label>
            <input
              type="checkbox"
              checked={ex.checkDeps}
              onChange={(e) => update({ export: { ...ex, checkDeps: e.target.checked } })}
            />
            导出前检查缺失依赖
          </label>
        </div>
      </div>
      <div className="empty">
        <h2>导入与导出</h2>
        <p>项目包包含素材清单、ContentVersion、Provenance 与渲染记录。导出前会检查缺失依赖。</p>
        <button type="button" className="btn" onClick={onCheck} disabled={busy} aria-busy={busy}>
          检查当前配置
        </button>
      </div>
    </>
  );
}

/* ---------- 局部辅助 ---------- */
