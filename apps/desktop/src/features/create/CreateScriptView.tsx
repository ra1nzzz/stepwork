/**
 * 创作页子视图 · 03 脚本创作
 * 真实接入 useScriptStore.generateScript / saveScript → GenerateScript / SaveScript 命令。
 *
 * 数据流：
 *   - useScriptStore.proposalVersionId + selectedAngleId 由上一步 angle 写入
 *   - 用户点击「AI 生成新版本」→ generateScript() → scriptTitle + seedBody + scriptVersionId
 *   - 用户编辑文本 → 防抖 800ms → saveScript({text}) → 新版本串链
 *   - 进入下一步渲染
 */

import { useEffect, useRef, useState } from "react";
import { useScriptStore } from "@/stores/useScriptStore";
import { useRenderStore } from "@/stores/useRenderStore";
import { useViewStore } from "@/stores/useViewStore";

export function CreateScriptView() {
  const setCreateSubView = useViewStore((s) => s.setCreateSubView);
  const scriptTitle = useScriptStore((s) => s.scriptTitle);
  const seedBody = useScriptStore((s) => s.seedBody);
  const scriptVersionId = useScriptStore((s) => s.scriptVersionId);
  const versionChain = useScriptStore((s) => s.versionChain);
  const proposalVersionId = useScriptStore((s) => s.proposalVersionId);
  const selectedAngleId = useScriptStore((s) => s.selectedAngleId);
  const isBusy = useScriptStore((s) => s.isBusy);
  const error = useScriptStore((s) => s.error);
  const generateScript = useScriptStore((s) => s.generateScript);
  const setScriptTitle = useScriptStore((s) => s.setScriptTitle);
  const saveScript = useScriptStore((s) => s.saveScript);

  const setRenderSourceVersion = useRenderStore((s) => s.setSourceVersion);

  const [body, setBody] = useState("");
  const [saveLabel, setSaveLabel] = useState("已就绪");
  const [activeTabIndex, setActiveTabIndex] = useState(0);
  const saveTimer = useRef<number | null>(null);

  // 当 store 推送新的 seedBody（AI 生成），同步到本地编辑框
  useEffect(() => {
    if (seedBody != null) {
      setBody(seedBody);
      setSaveLabel("AI 生成已载入");
    }
  }, [seedBody]);

  // 新版本生成后默认选中第一个（最新）tab
  useEffect(() => {
    setActiveTabIndex(0);
  }, [versionChain.length]);

  // 把 scriptVersionId 写入 renderStore，供下一步渲染
  useEffect(() => {
    if (scriptVersionId) {
      setRenderSourceVersion(scriptVersionId);
    }
  }, [scriptVersionId, setRenderSourceVersion]);

  function persistBody(value: string, title?: string) {
    setSaveLabel("正在保存…");
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(async () => {
      await saveScript(value, title ?? scriptTitle);
      setSaveLabel("已保存");
    }, 800);
  }

  function handleBodyChange(value: string) {
    setBody(value);
    persistBody(value);
  }

  function handleTitleBlur(e: React.FocusEvent<HTMLInputElement>) {
    // 标题失焦时立即保存（带当前正文）
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    void saveScript(body, e.target.value);
    setSaveLabel("已保存");
  }

  function handleGenerate() {
    void generateScript();
  }

  const canGenerate = !!proposalVersionId && !!selectedAngleId && !isBusy;
  const wordCount = body.length;

  return (
    <>
      <section className="page-head" data-od-id="studio-heading">
        <div>
          <p className="eyebrow">CONTENT VERSION</p>
          <h1>脚本的每次改写，都保留来源与理由</h1>
          <p className="page-subtitle">
            {proposalVersionId
              ? `基于角度 ${selectedAngleId ?? "?"} 生成脚本；编辑后自动保存为新版本。`
              : "请先在「原创角度」步骤选择角度，再回到此页生成脚本。"}
          </p>
        </div>
        <span className={`status ${error ? "danger" : isBusy ? "ai" : scriptVersionId ? "success" : "warning"}`}>
          {error ? "出错" : isBusy ? "生成中" : scriptVersionId ? "已生成" : "草稿"}
        </span>
      </section>

      <section className="layout-studio" data-od-id="studio-workspace-grid">
        {/* 中：脚本编辑器（占两列） */}
        <article className="panel editor" data-od-id="script-editor-panel">
          <div className="editor-toolbar">
            <div className="version-tabs" role="tablist" aria-label="脚本版本">
              {versionChain.length === 0 ? (
                <span className="panel-meta">尚无版本</span>
              ) : (
                versionChain.slice(0, 5).map((v, i) => (
                  <button
                    key={v.id}
                    className={`version-tab${activeTabIndex === i ? " active" : ""}`}
                    type="button"
                    role="tab"
                    aria-selected={activeTabIndex === i}
                    title={`${v.producer_kind ?? "user"} · ${v.created_at}`}
                    onClick={() => setActiveTabIndex(i)}
                  >
                    {v.producer_kind === "ai-script" ? "AI" : `V${versionChain.length - i}`}
                  </button>
                ))
              )}
            </div>
            <div className="filters">
              <span className="generation-state" role="status" aria-live="polite">
                {saveLabel}
              </span>
              <span className="panel-meta mono">{wordCount} 字</span>
              <button
                className="btn small ghost"
                type="button"
                onClick={() => {
                  void navigator.clipboard.writeText(body);
                  setSaveLabel("已复制");
                  window.setTimeout(() => setSaveLabel("已就绪"), 1200);
                }}
                disabled={!body}
              >
                复制脚本
              </button>
            </div>
          </div>

          <div className="panel-body provenance-bar" data-od-id="script-provenance">
            <dl className="provenance">
              <div className="provenance-row">
                <dt>当前版本</dt>
                <dd className="mono">
                  {scriptVersionId ? scriptVersionId.slice(0, 16) + "…" : "未保存"}
                </dd>
              </div>
              <div className="provenance-row">
                <dt>proposal</dt>
                <dd className="mono">
                  {proposalVersionId ? proposalVersionId.slice(0, 16) + "…" : "—"}
                </dd>
              </div>
              <div className="provenance-row">
                <dt>角度</dt>
                <dd>{selectedAngleId ?? "未选择"}</dd>
              </div>
              <div className="provenance-row">
                <dt>版本链</dt>
                <dd>{versionChain.length} 个版本</dd>
              </div>
            </dl>
          </div>

          <div className="panel-body" style={{ paddingTop: 0 }}>
            <div className="form-group" style={{ marginBottom: 12 }}>
              <label htmlFor="scriptTitle">标题</label>
              <input
                id="scriptTitle"
                className="field"
                type="text"
                value={scriptTitle}
                onChange={(e) => setScriptTitle(e.target.value)}
                onBlur={handleTitleBlur}
                placeholder="脚本标题"
              />
            </div>
            <div className="form-group">
              <label htmlFor="scriptBody">正文</label>
              <textarea
                id="scriptBody"
                className="field"
                value={body}
                onChange={(e) => handleBodyChange(e.target.value)}
                placeholder="点击「AI 生成新版本」生成脚本正文，或直接粘贴/编辑内容。"
                rows={16}
                style={{ width: "100%", resize: "vertical", fontFamily: "inherit", lineHeight: 1.7 }}
              />
            </div>
          </div>

          <div className="editor-toolbar">
            <span className="panel-meta">正文修改后 800ms 自动保存</span>
            <button
              className="btn small"
              type="button"
              onClick={handleGenerate}
              disabled={!canGenerate}
            >
              {isBusy ? "生成中…" : "AI 生成新版本"}
            </button>
          </div>
        </article>
      </section>

      {error && (
        <p className="error-text" style={{ color: "var(--danger)", marginTop: 12 }}>
          {error}
        </p>
      )}

      <div className="inline-actions" style={{ marginTop: 18 }}>
        <button
          type="button"
          className="btn ghost"
          onClick={() => setCreateSubView("angle")}
        >
          返回角度
        </button>
        <button
          type="button"
          className="btn primary"
          onClick={() => setCreateSubView("render")}
          disabled={!scriptVersionId}
        >
          生成视频草稿
        </button>
      </div>
    </>
  );
}
