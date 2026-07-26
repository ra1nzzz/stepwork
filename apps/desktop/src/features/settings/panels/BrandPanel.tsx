/** BrandProfile Tab：约束原创角度、脚本语气与事实表达。 */
import type { SettingsConfig } from "@/stores/useSettingsStore";
import {
  DEFAULT_OUTPUT_OPTIONS,
  MUST_EXECUTE_OPTIONS,
  toggleInList,
} from "./shared";

export function BrandPanel({
  settings,
  update,
}: {
  settings: SettingsConfig;
  update: (patch: Partial<SettingsConfig>) => void;
}) {
  const b = settings.brand;
  return (
    <>
      <div className="form-row">
        <label htmlFor="brandName">配置名称</label>
        <div>
          <input
            id="brandName"
            className="field w-full"
            value={b.name}
            onChange={(e) => update({ brand: { ...b, name: e.target.value } })}
          />
          <p className="form-help">当前工作区默认配置</p>
        </div>
      </div>
      <div className="form-row">
        <label htmlFor="audience">核心受众</label>
        <div>
          <input
            id="audience"
            className="field w-full" 
            value={b.audience}
            onChange={(e) => update({ brand: { ...b, audience: e.target.value } })}
          />
        </div>
      </div>
      <div className="form-row">
        <label htmlFor="tone">表达原则</label>
        <div>
          <textarea
            id="tone"
            className="textarea"
            value={b.tone}
            onChange={(e) => update({ brand: { ...b, tone: e.target.value } })}
          />
        </div>
      </div>
      <div className="form-row">
        <span className="row-label">必须执行</span>
        <div className="check-grid">
          {MUST_EXECUTE_OPTIONS.map((opt) => (
            <label key={opt.value}>
              <input
                type="checkbox"
                checked={b.mustExecute.includes(opt.value)}
                onChange={() =>
                  update({
                    brand: { ...b, mustExecute: toggleInList(b.mustExecute, opt.value) },
                  })
                }
              />
              {opt.label}
            </label>
          ))}
        </div>
      </div>
      <div className="form-row">
        <span className="row-label">默认输出</span>
        <div className="filters">
          {DEFAULT_OUTPUT_OPTIONS.map((opt) => {
            const on = b.defaultOutput.includes(opt.value);
            return (
              <span
                key={opt.value}
                className={`chip${on ? " active" : ""}`}
                role="button"
                tabIndex={0}
                aria-pressed={on}
                onClick={() =>
                  update({
                    brand: { ...b, defaultOutput: toggleInList(b.defaultOutput, opt.value) },
                  })
                }
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    update({
                      brand: { ...b, defaultOutput: toggleInList(b.defaultOutput, opt.value) },
                    });
                  }
                }}
              >
                {opt.label}
              </span>
            );
          })}
        </div>
      </div>
    </>
  );
}

/* ---------- 品牌档案（Tranche 2 · PRD-BRD-001/002） ---------- */

/** BrandProfile 编辑表单模型 */

