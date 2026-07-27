/**
 * 创作页子视图 · 02 原创角度
 * 真实接入 useScriptStore.generateTopics → GenerateTopic 命令。
 *
 * 数据流：
 *   - useScriptStore.sourceVersionId 由 CreateAnalysisView 在转写成功后写入
 *   - 用户点击「生成角度」→ generateTopics() → angles[] + proposalVersionId
 *   - 用户选中角度 → selectAngle(id)
 *   - 进入下一步脚本创作
 */

import { useScriptStore } from "@/stores/useScriptStore";
import { useViewStore } from "@/stores/useViewStore";

export function CreateAngleView() {
  const setCreateSubView = useViewStore((s) => s.setCreateSubView);
  const sourceVersionId = useScriptStore((s) => s.sourceVersionId);
  const angles = useScriptStore((s) => s.angles);
  const selectedAngleId = useScriptStore((s) => s.selectedAngleId);
  const isBusy = useScriptStore((s) => s.isBusy);
  const error = useScriptStore((s) => s.error);
  const generateTopics = useScriptStore((s) => s.generateTopics);
  const useBrandProfile = useScriptStore((s) => s.useBrandProfile);
  const duplicateWarnings = useScriptStore((s) => s.duplicateWarnings);
  const setUseBrandProfile = useScriptStore((s) => s.setUseBrandProfile);
  const selectAngle = useScriptStore((s) => s.selectAngle);

  const hasSource = !!sourceVersionId;
  const hasAngles = angles.length > 0;
  const canGenerate = hasSource && !isBusy;

  return (
    <>
      <section className="page-head" data-od-id="angle-heading">
        <div>
          <p className="eyebrow">ORIGINAL ANGLES</p>
          <h1>从分析证据中选择创作立场</h1>
          <p className="page-subtitle">
            {hasSource
              ? `基于版本 ${sourceVersionId.slice(0, 8)} 生成差异化角度，确认后进入脚本创作。`
              : "请先在「素材分析」步骤完成转写，再回到此页生成角度。"}
          </p>
        </div>
        <span
          className={`status ${
            error ? "danger" : isBusy ? "ai" : hasAngles ? "success" : "warning"
          }`}
        >
          {error
            ? "出错"
            : isBusy
              ? "生成中"
              : hasAngles
                ? `${angles.length} 个角度`
                : "未生成"}
        </span>
      </section>

      {/* PRD-SCR-004：与历史选题重复的提醒（只提示，不拦截） */}
      {duplicateWarnings.length > 0 && (
        <section className="section-gap" data-od-id="duplicate-warnings">
          <div className="panel" style={{ borderColor: "var(--warning)" }}>
            <div className="panel-body">
              <p className="panel-meta flush-top">
                ⚠️ 发现与历史选题相似的角度（仅提醒，可继续使用）：
              </p>
              <ul className="report-list">
                {duplicateWarnings.map((w, i) => (
                  <li key={`${w.ref_id}-${i}`}>{w.message}</li>
                ))}
              </ul>
            </div>
          </div>
        </section>
      )}

      {/* PRD-BRD-002：生成时可选择是否启用项目绑定的品牌档 */}
      <section className="section-gap">
        <label className="panel-meta" style={{ display: "inline-flex", gap: 6 }}>
          <input
            type="checkbox"
            checked={useBrandProfile}
            onChange={(e) => setUseBrandProfile(e.target.checked)}
            disabled={isBusy}
          />
          生成时启用项目品牌档（定位 / 语气 / 内容支柱 / 禁用表达）
        </label>
      </section>

      <section className="layout-main" data-od-id="angle-layout">
        <div
          className="angle-grid"
          role="radiogroup"
          aria-label="原创角度候选"
          data-od-id="angle-grid"
        >
          {!hasAngles && !isBusy && (
            <article className="angle-card" style={{ cursor: "default" }}>
              <span className="topic-score">等待生成</span>
              <h3>尚无原创角度</h3>
              <p>
                {hasSource
                  ? "点击右上方「重新生成」按钮，AI 将基于素材分析生成多个差异化角度。"
                  : "请先回到「素材分析」完成转写。"}
              </p>
            </article>
          )}

          {isBusy && (
            <article className="angle-card" style={{ cursor: "default" }}>
              <span className="topic-score ai">生成中</span>
              <h3>正在调用 AI 生成角度…</h3>
              <p>基于素材分析与 BrandProfile 生成差异化角度，请稍候。</p>
            </article>
          )}

          {angles.map((angle, idx) => (
            <article
              key={angle.id}
              className={`angle-card${angle.id === selectedAngleId ? " selected" : ""}`}
              tabIndex={0}
              role="radio"
              aria-checked={angle.id === selectedAngleId}
              data-title={angle.title}
              data-od-id={`angle-card-${idx + 1}`}
              onClick={() => selectAngle(angle.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  selectAngle(angle.id);
                }
              }}
            >
              <span className="topic-score">角度 {idx + 1}</span>
              <h3>{angle.title}</h3>
              <p>{angle.rationale}</p>
              {/* PRD-SCR-001：每个角度展示受众、观点、差异与风险 */}
              <dl className="angle-facts">
                {angle.hook && (
                  <div>
                    <dt>钩子</dt>
                    <dd>{angle.hook}</dd>
                  </div>
                )}
                {angle.audience && (
                  <div>
                    <dt>受众</dt>
                    <dd>{angle.audience}</dd>
                  </div>
                )}
                {angle.stance && (
                  <div>
                    <dt>观点</dt>
                    <dd>{angle.stance}</dd>
                  </div>
                )}
                {angle.risks && angle.risks.length > 0 && (
                  <div>
                    <dt>风险</dt>
                    <dd>{angle.risks.join("；")}</dd>
                  </div>
                )}
              </dl>
            </article>
          ))}
        </div>

        <aside className="grid">
          <article className="panel" data-od-id="angle-source-info">
            <div className="panel-head">
              <div>
                <h2 className="panel-title">来源版本</h2>
                <div className="panel-meta">选题来源</div>
              </div>
            </div>
            <div className="panel-body">
              <dl className="provenance">
                <div className="provenance-row">
                  <dt>source_version_id</dt>
                  <dd className="mono">
                    {sourceVersionId ? sourceVersionId.slice(0, 16) + "…" : "未设置"}
                  </dd>
                </div>
                <div className="provenance-row">
                  <dt>角度数量</dt>
                  <dd>{angles.length}</dd>
                </div>
                <div className="provenance-row">
                  <dt>已选择</dt>
                  <dd>
                    {selectedAngleId
                      ? angles.find((a) => a.id === selectedAngleId)?.title.slice(0, 24) + "…"
                      : "未选择"}
                  </dd>
                </div>
              </dl>
            </div>
          </article>

          {error && (
            <article className="panel">
              <div className="panel-head">
                <div>
                  <h2 className="panel-title">错误</h2>
                </div>
                <span className="status danger">失败</span>
              </div>
              <div className="panel-body">
                <p style={{ color: "var(--danger)", margin: 0 }}>{error}</p>
              </div>
            </article>
          )}
        </aside>
      </section>

      <div className="inline-actions gap-top-lg">
        <button
          type="button"
          className="btn ghost"
          onClick={() => void generateTopics()}
          disabled={!canGenerate}
        >
          {isBusy ? "生成中…" : hasAngles ? "重新生成" : "生成角度"}
        </button>
        <button
          type="button"
          className="btn primary"
          onClick={() => setCreateSubView("script")}
          disabled={!selectedAngleId}
        >
          采用此角度
        </button>
      </div>
    </>
  );
}
