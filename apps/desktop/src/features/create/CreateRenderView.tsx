/**
 * 创作页子视图 · 04 视频草稿
 * 真实接入 useRenderStore.render / cancel / retry → CreateRenderJob / CancelJob 命令。
 *
 * 数据流：
 *   - useRenderStore.sourceVersionId 由 CreateScriptView 在脚本保存后写入
 *   - 用户选择 TTS 引擎 → setTtsEngine；用户录音经 dialog 选择音频文件
 *     → setUserAudioUri → payload.user_audio_uri（Tranche 2）
 *   - 用户点击「开始渲染」→ render() → status + progress + draft
 *   - 渲染中可取消 → cancel()；失败可重试 → retry()
 *   - 渲染成功展示 artifacts（video/subtitles/audio）+ 打开输出目录（Tranche 2）
 *   - 费用透明：执行前展示 TTS provider/model，执行后展示 detail.invocation
 */

import { useEffect, useState } from "react";
import { useRenderStore, type RenderStatus } from "@/stores/useRenderStore";
import { useViewStore } from "@/stores/useViewStore";
import {
  buildEnvelope,
  dispatchCommand,
  getWorkspaceId,
  isTauri,
} from "@/lib/tauri";
import { dirname, openLocalPath } from "@/lib/shell";
import { estimateCost, formatCost, useProviderInfo } from "@/lib/useProviderInfo";

/** 用户录音可选的音频扩展名（与导入的 AUDIO_EXTS 一致） */
const AUDIO_EXTS = ["mp3", "wav", "m4a", "flac", "aac", "ogg", "opus"];

/** 从绝对路径取文件名（Windows / POSIX 分隔符都兼容） */
function basename(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

function statusLabel(s: RenderStatus): string {
  switch (s) {
    case "idle": return "尚未渲染";
    case "running": return "渲染中";
    case "succeeded": return "渲染完成";
    case "failed": return "失败";
    case "cancelled": return "已取消";
    default: return s;
  }
}

function statusClass(s: RenderStatus): string {
  switch (s) {
    case "running": return "ai";
    case "succeeded": return "success";
    case "failed": return "danger";
    case "cancelled": return "warning";
    default: return "warning";
  }
}

export function CreateRenderView() {
  const setView = useViewStore((s) => s.setView);
  const sourceVersionId = useRenderStore((s) => s.sourceVersionId);
  const template = useRenderStore((s) => s.template);
  const aspect = useRenderStore((s) => s.aspect);
  const ttsEngine = useRenderStore((s) => s.ttsEngine);
  const userAudioUri = useRenderStore((s) => s.userAudioUri);
  const status = useRenderStore((s) => s.status);
  const jobId = useRenderStore((s) => s.jobId);
  const progress = useRenderStore((s) => s.progress);
  const draft = useRenderStore((s) => s.draft);
  const artifacts = useRenderStore((s) => s.artifacts);
  const invocation = useRenderStore((s) => s.invocation);
  const ttsInvocation = useRenderStore((s) => s.ttsInvocation);
  const isBusy = useRenderStore((s) => s.isBusy);
  const error = useRenderStore((s) => s.error);
  const setTemplate = useRenderStore((s) => s.setTemplate);
  const setAspect = useRenderStore((s) => s.setAspect);
  const setTtsEngine = useRenderStore((s) => s.setTtsEngine);
  const setUserAudioUri = useRenderStore((s) => s.setUserAudioUri);
  const render = useRenderStore((s) => s.render);
  const cancel = useRenderStore((s) => s.cancel);
  const retry = useRenderStore((s) => s.retry);

  const providerInfo = useProviderInfo();
  const [openNotice, setOpenNotice] = useState<string | null>(null);
  // PRD-REN-005：模板与画幅清单来自后端注册表（ListRenderTemplates）
  const [templates, setTemplates] = useState<
    { id: string; label: string; default_aspect: string }[]
  >([]);
  const [aspects, setAspects] = useState<
    { id: string; resolution: [number, number] }[]
  >([]);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const env = buildEnvelope(
          "ListRenderTemplates",
          getWorkspaceId(),
          null,
          {},
        );
        const res = await dispatchCommand(env);
        if (cancelled || !res.ok) return;
        const d = (res.detail ?? {}) as {
          templates?: { id: string; label: string; default_aspect: string }[];
          aspects?: { id: string; resolution: [number, number] }[];
        };
        setTemplates(d.templates ?? []);
        setAspects(d.aspects ?? []);
      } catch {
        /* 后端未连接：保持空列表，渲染按钮仍可用（后端有默认模板） */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // PRD-REN-002：执行前旁白费用需要源文本字符量（经 GetContentVersion 取）
  const [sourceCharCount, setSourceCharCount] = useState(0);

  const selectedProjectId = useViewStore((s) => s.selectedProjectId);
  useEffect(() => {
    let cancelled = false;
    if (!sourceVersionId) {
      setSourceCharCount(0);
      return;
    }
    void (async () => {
      try {
        const env = buildEnvelope(
          "GetContentVersion",
          getWorkspaceId(),
          selectedProjectId ?? null,
          { versionId: sourceVersionId },
        );
        const res = await dispatchCommand(env);
        if (cancelled || !res.ok) return;
        const detail = (res.detail ?? {}) as {
          version?: { content?: string };
        };
        setSourceCharCount((detail.version?.content ?? "").length);
      } catch {
        /* 拉取失败不影响渲染，仅费用预估显示为未知字数 */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sourceVersionId, selectedProjectId]);
  const inTauri = isTauri();

  const canRender =
    !!sourceVersionId &&
    !isBusy &&
    (ttsEngine !== "user_audio" || !!userAudioUri);
  const progressPercent = Math.round(progress * 100);

  /** 用户录音选择：dialog 选音频文件 → user_audio_uri（Tranche 2） */
  async function pickUserAudio() {
    if (!inTauri || isBusy) return;
    const { open } = await import("@tauri-apps/plugin-dialog");
    const selected = await open({
      multiple: false,
      filters: [{ name: "音频", extensions: AUDIO_EXTS }],
    });
    if (typeof selected === "string") setUserAudioUri(selected);
  }

  /** 打开输出目录（视频 artifact 所在目录） */
  async function handleOpenOutputDir() {
    const video = artifacts?.video ?? draft?.video_uri;
    if (!video) return;
    try {
      setOpenNotice(null);
      await openLocalPath(dirname(video));
    } catch (e) {
      setOpenNotice(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <>
      <section className="page-head" data-od-id="render-heading">
        <div>
          <p className="eyebrow">DRAFT RENDER · 9:16</p>
          <h1>先生成可审阅的视频草稿</h1>
          <p className="page-subtitle">
            {sourceVersionId
              ? `基于版本 ${sourceVersionId.slice(0, 8)} 渲染竖屏视频草稿。`
              : "请先在「脚本创作」步骤保存脚本，再回到此页渲染。"}
          </p>
        </div>
        <span className={`status ${statusClass(status)}`}>
          {statusLabel(status)}
        </span>
      </section>

      <section className="render-layout" data-od-id="render-workspace">
        <article className="panel">
          <div className="panel-head">
            <div>
              <h2 className="panel-title">9:16 预览</h2>
              <div className="panel-meta">
                {draft ? `${draft.resolution[0]} × ${draft.resolution[1]} · ${draft.fps}fps` : "1080 × 1920 · 30fps"}
              </div>
            </div>
            <span className={`status ${statusClass(status)}`}>{statusLabel(status)}</span>
          </div>
          <div className="panel-body">
            <div className="phone-preview" id="phonePreview">
              <div className="preview-copy">
                <p className="eyebrow">视频草稿</p>
                <h2>
                  {status === "succeeded" && draft?.video_uri
                    ? "草稿已生成"
                    : status === "running"
                      ? `渲染中 ${progressPercent}%`
                      : "尚未渲染"}
                </h2>
                <p>
                  {status === "succeeded" && draft
                    ? `模板：${draft.template} · TTS：${draft.tts_engine}`
                    : "配置语音与字幕后点击「开始渲染」。"}
                </p>
                {draft?.video_uri && (
                  <p className="panel-meta mono" style={{ marginTop: 8, fontSize: 11 }}>
                    {draft.video_uri}
                  </p>
                )}
              </div>
            </div>
          </div>
        </article>

        <div className="render-controls">
          <article className="panel">
            <div className="panel-head">
              <div>
                <h2 className="panel-title">语音与字幕</h2>
                <div className="panel-meta">选择 TTS 引擎或上传用户录音</div>
              </div>
            </div>
            <div className="panel-body grid">
              <div className="form-group">
                <label htmlFor="ttsEngine">语音来源</label>
                <select
                  id="ttsEngine"
                  className="select"
                  value={ttsEngine}
                  onChange={(e) => setTtsEngine(e.target.value as "synthesize" | "user_audio")}
                  disabled={isBusy}
                >
                  <option value="synthesize">Edge TTS · 合成语音</option>
                  <option value="user_audio">用户录音</option>
                </select>
              </div>
              {ttsEngine === "user_audio" && (
                <div className="form-group">
                  <label htmlFor="userAudioPick">用户录音文件</label>
                  <div className="inline-actions">
                    <button
                      id="userAudioPick"
                      type="button"
                      className="btn small ghost"
                      onClick={() => void pickUserAudio()}
                      disabled={!inTauri || isBusy}
                      title={inTauri ? undefined : "选择音频文件需要在桌面应用中使用"}
                    >
                      {userAudioUri ? "重新选择" : "选择音频文件"}
                    </button>
                    <span className="panel-meta mono" style={{ alignSelf: "center" }}>
                      {userAudioUri ? basename(userAudioUri) : "未选择"}
                    </span>
                  </div>
                </div>
              )}
              <div className="form-group">
                <label htmlFor="template">模板</label>
                <select
                  id="template"
                  className="select"
                  value={template}
                  onChange={(e) => {
                    const id = e.target.value;
                    setTemplate(id);
                    // 切模板时联动其默认画幅（否则选横屏模板仍渲成 9:16）
                    const tpl = templates.find((t) => t.id === id);
                    if (tpl?.default_aspect) setAspect(tpl.default_aspect);
                  }}
                  disabled={isBusy || templates.length === 0}
                >
                  {/* PRD-REN-005：选项来自后端注册表，避免 UI 有选项、
                      后端不认（旧实现下拉切换后画面完全相同） */}
                  {templates.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="form-group">
                <label htmlFor="aspect">画幅比例</label>
                <select
                  id="aspect"
                  className="select"
                  value={aspect}
                  onChange={(e) => setAspect(e.target.value)}
                  disabled={isBusy || aspects.length === 0}
                >
                  {aspects.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.id}（{a.resolution[0]}×{a.resolution[1]}）
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </article>

          <article className="panel">
            <div className="panel-head">
              <div>
                <h2 className="panel-title">渲染任务</h2>
                <div className="panel-meta" id="renderSummary">
                  {status === "running"
                    ? "正在渲染…"
                    : status === "succeeded"
                      ? "已完成"
                      : status === "failed"
                        ? "渲染失败"
                        : status === "cancelled"
                          ? "已取消"
                          : "等待开始"}
                </div>
              </div>
              <span className="mono panel-meta" id="renderPercent">
                {progressPercent}%
              </span>
            </div>
            <div className="panel-body">
              <div
                className="progress"
                id="renderProgress"
                style={{ ["--progress" as string]: `${progressPercent}%` }}
              >
                <span />
              </div>
              <dl className="provenance section-gap">
                <div className="provenance-row">
                  <dt>源版本</dt>
                  <dd className="mono">
                    {sourceVersionId ? sourceVersionId.slice(0, 16) + "…" : "未设置"}
                  </dd>
                </div>
                <div className="provenance-row">
                  <dt>模板</dt>
                  <dd>{template}</dd>
                </div>
                <div className="provenance-row">
                  <dt>TTS</dt>
                  <dd>
                    {ttsEngine === "synthesize"
                      ? (providerInfo?.tts.provider ?? "合成旁白")
                      : "用户录音"}
                  </dd>
                </div>
                {/* 费用透明：执行前展示将使用的 TTS provider/model 与预计费用 */}
                {ttsEngine === "synthesize" && (
                  <>
                    <div className="provenance-row">
                      <dt>将使用模型</dt>
                      <dd className="mono">
                        {providerInfo
                          ? `${providerInfo.tts.provider ?? "未配置"} / ${providerInfo.tts.model ?? "未配置"}`
                          : "读取配置中…"}
                      </dd>
                    </div>
                    <div className="provenance-row">
                      <dt>预计旁白费用</dt>
                      <dd>
                        {providerInfo
                          ? formatCost(
                              estimateCost(
                                sourceCharCount,
                                providerInfo.ttsCostPer1k,
                              ),
                            )
                          : "读取配置中…"}
                        {sourceCharCount > 0 && `（${sourceCharCount} 字粗估）`}
                      </dd>
                    </div>
                  </>
                )}
                {/* 费用透明：执行后展示 detail.invocation（渲染器，本机无费用） */}
                {invocation && (
                  <div className="provenance-row">
                    <dt>渲染</dt>
                    <dd>
                      {invocation.provider} / {invocation.model}
                      {" · "}
                      {formatCost(invocation.estimated_cost)}
                    </dd>
                  </div>
                )}
                {/* PRD-REN-002：执行后旁白合成的真实来源与费用 */}
                {ttsInvocation && (
                  <div className="provenance-row">
                    <dt>本次旁白合成</dt>
                    <dd>
                      {ttsInvocation.provider} / {ttsInvocation.model}
                      {" · "}
                      {formatCost(ttsInvocation.estimated_cost)}
                    </dd>
                  </div>
                )}
                {jobId && (
                  <div className="provenance-row">
                    <dt>job_id</dt>
                    <dd className="mono">{jobId.slice(0, 16)}…</dd>
                  </div>
                )}
              </dl>
              <div className="task-actions">
                {status === "running" && (
                  <button
                    className="btn small ghost"
                    type="button"
                    onClick={() => void cancel()}
                  >
                    取消
                  </button>
                )}
                {status === "failed" && (
                  <button
                    className="btn small"
                    type="button"
                    onClick={() => void retry()}
                  >
                    从失败重试
                  </button>
                )}
                <a
                  className="btn small ghost"
                  onClick={() => setView("tasks")}
                  style={{ cursor: "pointer" }}
                >
                  查看任务
                </a>
              </div>
            </div>
          </article>

          <article className="panel">
            <div className="panel-head">
              <div>
                <h2 className="panel-title">输出 Artifact</h2>
                <div className="panel-meta">渲染完成后可用</div>
              </div>
              <span className={`status ${status === "succeeded" ? "success" : "warning"}`}>
                {status === "succeeded" ? "可导出" : "等待渲染"}
              </span>
            </div>
            <div className="panel-body export-list">
              <div className="export-row">
                <div>
                  <strong>视频草稿 MP4</strong>
                  <div className="row-sub">
                    {draft ? `${draft.resolution[0]} × ${draft.resolution[1]}` : "1080 × 1920 · H.264"}
                  </div>
                </div>
                <button
                  className="btn small"
                  type="button"
                  disabled={status !== "succeeded" || !(artifacts?.video ?? draft?.video_uri)}
                  onClick={() => {
                    const video = artifacts?.video ?? draft?.video_uri;
                    if (video) void navigator.clipboard.writeText(video);
                  }}
                >
                  复制路径
                </button>
              </div>
              {/* Tranche 2：字幕/音频 artifact（渲染成功后由 detail.artifacts 提供） */}
              {artifacts?.subtitles && (
                <div className="export-row">
                  <div>
                    <strong>字幕 SRT</strong>
                    <div className="row-sub mono">{basename(artifacts.subtitles)}</div>
                  </div>
                  <button
                    className="btn small"
                    type="button"
                    onClick={() => {
                      void navigator.clipboard.writeText(artifacts.subtitles as string);
                    }}
                  >
                    复制路径
                  </button>
                </div>
              )}
              {artifacts?.audio && (
                <div className="export-row">
                  <div>
                    <strong>旁白音频</strong>
                    <div className="row-sub mono">{basename(artifacts.audio)}</div>
                  </div>
                  <button
                    className="btn small"
                    type="button"
                    onClick={() => {
                      void navigator.clipboard.writeText(artifacts.audio as string);
                    }}
                  >
                    复制路径
                  </button>
                </div>
              )}
              {status === "succeeded" && (artifacts?.video ?? draft?.video_uri) && (
                <div className="inline-actions section-gap">
                  <button
                    className="btn small"
                    type="button"
                    onClick={() => void handleOpenOutputDir()}
                  >
                    打开输出目录
                  </button>
                </div>
              )}
              {openNotice && (
                <p className="panel-meta section-gap" role="status">
                  {openNotice}
                </p>
              )}
            </div>
          </article>
        </div>
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
          onClick={() => useViewStore.getState().setCreateSubView("script")}
        >
          返回脚本
        </button>
        <button
          type="button"
          className="btn primary"
          id="renderButton"
          disabled={!canRender}
          onClick={() => void render()}
        >
          {isBusy ? "渲染中…" : status === "succeeded" ? "重新渲染" : "开始渲染"}
        </button>
      </div>
    </>
  );
}
