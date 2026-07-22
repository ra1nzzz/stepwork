/**
 * 选题角度视图（W5 Batch1）
 * - 基于转写/素材 dispatch GenerateTopic → 3 个差异化角度
 * - 选中角度后 dispatch GenerateScript → 落 script 版并 seed 编辑器
 */

import { useScriptStore } from "@/stores/useScriptStore";

export function TopicView() {
  const angles = useScriptStore((s) => s.angles);
  const selectedAngleId = useScriptStore((s) => s.selectedAngleId);
  const generateTopics = useScriptStore((s) => s.generateTopics);
  const generateScript = useScriptStore((s) => s.generateScript);
  const selectAngle = useScriptStore((s) => s.selectAngle);
  const isBusy = useScriptStore((s) => s.isBusy);
  const error = useScriptStore((s) => s.error);
  const seedBody = useScriptStore((s) => s.seedBody);

  return (
    <section className="feature-view" data-od-id="topic-view">
      <header className="feature-head">
        <h2>选题角度</h2>
        <p className="feature-sub">基于转写/素材生成 3 个差异化角度</p>
      </header>

      <div className="topic-actions">
        <button
          type="button"
          className="btn primary"
          disabled={isBusy}
          onClick={() => void generateTopics()}
        >
          {angles.length ? "重新生成角度" : "生成选题角度"}
        </button>
        <button
          type="button"
          className="btn"
          disabled={isBusy || angles.length === 0}
          onClick={() => void generateScript()}
        >
          选定角度 → 生成脚本
        </button>
      </div>

      {error && (
        <p className="error-text" data-od-id="topic-error">
          {error}
        </p>
      )}

      <ul className="angle-list">
        {angles.map((a) => (
          <li
            key={a.id}
            className={`angle-card${selectedAngleId === a.id ? " active" : ""}`}
            data-od-id={`angle-${a.id}`}
            onClick={() => selectAngle(a.id)}
          >
            <h3>{a.title}</h3>
            <p className="angle-rationale">{a.rationale}</p>
            <p className="angle-hook">钩子：{a.hook}</p>
          </li>
        ))}
      </ul>

      {seedBody != null && (
        <p className="seed-hint" data-od-id="seed-hint">
          已生成脚本草稿，可在右侧编辑器继续润色（自动保存）。
        </p>
      )}
    </section>
  );
}
