/**
 * 创作页子视图 · 03 脚本创作
 * 真实接入 useScriptStore.generateScript / saveScript → GenerateScript / SaveScript 命令。
 *
 * 数据流：
 *   - useScriptStore.proposalVersionId + selectedAngleId 由上一步 angle 写入
 *   - 用户点击「AI 生成新版本」→ generateScript() → scriptTitle + seedBody + scriptVersionId
 *   - 用户编辑文本 → 防抖 800ms → saveScript({text}) → 新版本串链
 *   - 进入下一步渲染
 *
 * Tranche 2（WS-005 恢复闭环）：
 *   - 进入创作页时 loadVersions() → ListContentVersions 恢复最近 script 版本
 *   - 点击版本 tab → GetContentVersion 加载该版本全文（只读预览）
 *   - 回滚 = 以历史内容 SaveScript 生成新版本，绝不覆盖历史
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useScriptStore } from "@/stores/useScriptStore";

/** PRD-SCR-003 四种段落级操作（与后端 OPERATIONS 一一对应） */
const PARAGRAPH_OPS = [
  { id: "rewrite" as const, label: "重写" },
  { id: "expand" as const, label: "扩写" },
  { id: "condense" as const, label: "压缩" },
  { id: "generate" as const, label: "生成" },
];
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
  const loadVersions = useScriptStore((s) => s.loadVersions);
  const loadVersionContent = useScriptStore((s) => s.loadVersionContent);
  const editParagraph = useScriptStore((s) => s.editParagraph);

  const setRenderSourceVersion = useRenderStore((s) => s.setSourceVersion);

  const [body, setBody] = useState("");
  const [saveLabel, setSaveLabel] = useState("已就绪");
  const [activeTabIndex, setActiveTabIndex] = useState(0);
  /** 历史版本只读预览（点击非最新 tab 时加载） */
  const [preview, setPreview] = useState<{
    id: string;
    title: string;
    body: string;
  } | null>(null);
  const saveTimer = useRef<number | null>(null);

  // 进入创作页时恢复最近的 script 版本链（WS-005 恢复闭环）
  useEffect(() => {
    if (useScriptStore.getState().versionChain.length === 0) {
      void loadVersions();
    }
  }, [loadVersions]);

  // 当 store 推送新的 seedBody（AI 生成 / 版本恢复），同步到本地编辑框
  useEffect(() => {
    if (seedBody != null) {
      setBody(seedBody);
      setSaveLabel("版本已载入");
    }
  }, [seedBody]);

  // 新版本生成后默认选中第一个（最新）tab，并退出历史预览
  useEffect(() => {
    setActiveTabIndex(0);
    setPreview(null);
  }, [versionChain.length]);

  /** 版本 tab 点击：最新 tab 回到编辑态；历史 tab 拉全文进入只读预览 */
  async function handleTabClick(index: number, versionId: string) {
    setActiveTabIndex(index);
    if (index === 0) {
      setPreview(null);
      return;
    }
    const content = await loadVersionContent(versionId);
    if (content) {
      setPreview({ id: versionId, title: content.title, body: content.body });
    } else {
      setPreview(null);
      setSaveLabel("版本加载失败");
    }
  }

  /** 回滚：以历史版本内容 SaveScript 生成新版本（不覆盖历史） */
  async function handleRollback() {
    if (!preview) return;
    const title = preview.title || scriptTitle;
    setScriptTitle(title);
    setBody(preview.body);
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    await saveScript(preview.body, title);
    setPreview(null);
    setActiveTabIndex(0);
    setSaveLabel("已回滚为新版本");
  }

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

  // 与后端 worker/runtime/script/paragraph.py 的 split_paragraphs 同规则
  // （按空行切分、去空段），否则 paragraph_index 会对不上。
  const paragraphs = useMemo(
    () =>
      body
        .split(/\n\s*\n+/)
        .map((t) => t.trim())
        .filter(Boolean),
    [body],
  );

  async function handleParagraphOp(
    index: number,
    operation: "rewrite" | "expand" | "condense" | "generate",
  ) {
    const updated = await editParagraph(index, operation);
    if (updated !== null) {
      // 后端已落新版本，这里只同步编辑器显示，不再触发自动保存
      setBody(updated);
      setSaveLabel("已保存");
    }
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
                    onClick={() => void handleTabClick(i, v.id)}
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
            {preview ? (
              <>
                {/* 历史版本只读预览 + 回滚（回滚生成新版本，不覆盖历史） */}
                <div className="form-group" style={{ marginBottom: 12 }}>
                  <label htmlFor="previewTitle">
                    历史版本标题（只读 · {preview.id.slice(0, 8)}…）
                  </label>
                  <input
                    id="previewTitle"
                    className="field"
                    type="text"
                    value={preview.title}
                    readOnly
                    placeholder="（无标题）"
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="previewBody">历史版本正文（只读）</label>
                  <textarea
                    id="previewBody"
                    className="field"
                    value={preview.body}
                    readOnly
                    rows={16}
                    style={{ width: "100%", resize: "vertical", fontFamily: "inherit", lineHeight: 1.7 }}
                  />
                </div>
              </>
            ) : (
              <>
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

                {/* PRD-SCR-003：段落级生成/重写/扩写/压缩。
                    每次操作生成新版本，可在右侧版本历史回滚撤销。 */}
                {paragraphs.length > 0 && (
                  <div className="form-group">
                    <label>段落操作</label>
                    <p className="panel-meta" style={{ marginTop: 0 }}>
                      共 {paragraphs.length} 段（按空行切分）。每次操作生成新版本，
                      可在「版本历史」回滚撤销。
                      {!scriptVersionId && " 请先保存脚本后再操作。"}
                    </p>
                    <div className="paragraph-ops">
                      {paragraphs.map((para, idx) => (
                        <div key={idx} className="paragraph-op-row">
                          <span className="panel-meta mono">#{idx + 1}</span>
                          <span className="paragraph-op-text">
                            {para.length > 60 ? `${para.slice(0, 60)}…` : para}
                          </span>
                          <span className="inline-actions">
                            {PARAGRAPH_OPS.map((op) => (
                              <button
                                key={op.id}
                                type="button"
                                className="btn small ghost"
                                disabled={isBusy || !scriptVersionId}
                                onClick={() => void handleParagraphOp(idx, op.id)}
                                title={op.label}
                              >
                                {op.label}
                              </button>
                            ))}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>

          <div className="editor-toolbar">
            {preview ? (
              <>
                <span className="panel-meta">
                  正在查看历史版本，回滚将以此内容生成新版本
                </span>
                <button
                  className="btn small primary"
                  type="button"
                  onClick={() => void handleRollback()}
                >
                  回滚到此版本
                </button>
              </>
            ) : (
              <>
                <span className="panel-meta">正文修改后 800ms 自动保存</span>
                <button
                  className="btn small"
                  type="button"
                  onClick={handleGenerate}
                  disabled={!canGenerate}
                >
                  {isBusy ? "生成中…" : "AI 生成新版本"}
                </button>
              </>
            )}
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
