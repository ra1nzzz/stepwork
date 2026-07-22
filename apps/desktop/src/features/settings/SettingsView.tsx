/**
 * SET.3 — SettingsView
 * 4 个 Tab（对齐 Prototype/settings.html）：BrandProfile / AI Provider / 数据与存储 / 导入与导出。
 * 所有字段绑定 useSettingsStore。API Key 仅以 password 控件呈现，绝不回显明文。
 */
import { useMemo, useState, useEffect } from "react";
import { useSettingsStore } from "@/stores/useSettingsStore";
import type { SettingsConfig } from "@/stores/useSettingsStore";
import { getConfig, updateConfig, DEV_BRIDGE, isTauri } from "@/lib/tauri";

import "@/styles/settings.css";

/** 主代理在 tauri.ts 提供的返回类型（此处标注，供调用点复用） */
type ConfigResult = { ok: boolean; config?: unknown; resolved?: unknown; error?: string };

type TabKey = "brand" | "providers" | "data" | "export";

const TABS: { key: TabKey; label: string; meta: string }[] = [
  { key: "brand", label: "BrandProfile", meta: "用于约束原创角度、脚本语气与事实表达" },
  { key: "providers", label: "AI Provider", meta: "查看任务使用的模型、费用与数据范围" },
  { key: "data", label: "数据与存储", meta: "管理素材、Artifact 与诊断日志的保留策略" },
  { key: "export", label: "导入与导出", meta: "校验项目包及其生成记录的完整性" },
];

const MUST_EXECUTE_OPTIONS: { value: string; label: string }[] = [
  { value: "cite-sources", label: "标注事实来源与时间戳" },
  { value: "check-similarity", label: "检查历史内容相似度" },
  { value: "human-confirm-risk", label: "高风险判断需人工确认" },
];

const DEFAULT_OUTPUT_OPTIONS: { value: string; label: string }[] = [
  { value: "<=90s", label: "90 秒以内" },
  { value: "9:16", label: "9:16 竖屏" },
  { value: "voiceover+broll", label: "口播 + B-roll" },
];

export default function SettingsView() {
  const settings = useSettingsStore((s) => s.settings);
  const update = useSettingsStore((s) => s.update);
  const markSaved = useSettingsStore((s) => s.markSaved);

  const [activeTab, setActiveTab] = useState<TabKey>("brand");
  const [leaving, setLeaving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ kind: "success" | "danger" | "warning"; text: string } | null>(null);
  const [resolved, setResolved] = useState<unknown>(null);
  const [showKeys, setShowKeys] = useState<Record<string, boolean>>({});

  // 重载后回灌后端已存配置（掩码态），使 UI 与后端密钥覆盖层对齐，
  // 避免「字段显示空串但后端其实持有密钥」的错位（qa P0 两层对齐）。
  useEffect(() => {
    if (!DEV_BRIDGE && !isTauri()) return; // mock 模式无需回灌
    let cancelled = false;
    (async () => {
      try {
        const res = (await getConfig()) as ConfigResult;
        if (cancelled) return;
        if (res.ok && res.config && typeof res.config === "object") {
          update(res.config as Partial<SettingsConfig>);
        }
      } catch {
        /* 桥未就绪：忽略，使用本地默认 */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [update]);

  const visibleTab = useMemo(() => TABS.find((t) => t.key === activeTab)!, [activeTab]);

  function switchTab(key: TabKey) {
    if (key === activeTab) return;
    setLeaving(true);
    window.setTimeout(() => {
      setActiveTab(key);
      setLeaving(false);
    }, 150);
  }

  function toggleKeyVisibility(field: string) {
    setShowKeys((prev) => ({ ...prev, [field]: !prev[field] }));
  }

  /** 「检查当前配置」：读取后端解析出的 provider（已脱敏掩码） */
  async function handleCheck() {
    setBusy(true);
    setStatus(null);
    try {
      const res = (await getConfig()) as ConfigResult;
      if (res.ok) {
        setResolved(res.resolved ?? res.config ?? null);
        setStatus({ kind: "success", text: "配置检查完成" });
      } else {
        setResolved(null);
        setStatus({ kind: "danger", text: res.error ?? "检查失败" });
      }
    } catch (e) {
      setResolved(null);
      setStatus({ kind: "danger", text: String(e) });
    } finally {
      setBusy(false);
    }
  }

  /** 「保存」：把当前 store 差异上行；按钮期间 disabled + aria-busy */
  async function handleSave() {
    setBusy(true);
    setStatus(null);
    try {
      const res = (await updateConfig(settings as SettingsConfig)) as ConfigResult;
      if (res.ok) {
        markSaved();
        setStatus({ kind: "success", text: "工作区设置已保存" });
      } else {
        setStatus({ kind: "danger", text: res.error ?? "保存失败" });
      }
    } catch (e) {
      setStatus({ kind: "danger", text: String(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="settings-view">
      <section className="page-head">
        <div>
          <p className="eyebrow">WORKSPACE SETTINGS</p>
          <h1>品牌、模型与数据边界</h1>
          <p className="page-subtitle">
            BrandProfile 会约束角度和脚本生成；Provider 与数据保留策略在任务开始前始终可见。
          </p>
        </div>
        {status && (
          <span className={`status ${status.kind}`} role="status" aria-live="polite">
            {status.text}
          </span>
        )}
      </section>

      <section className="settings-grid">
        <aside className="panel settings-menu" role="tablist" aria-orientation="vertical">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={activeTab === t.key}
              aria-controls="settingsContent"
              className={activeTab === t.key ? "active" : ""}
              onClick={() => switchTab(t.key)}
            >
              {t.label}
            </button>
          ))}
        </aside>

        <article className="panel" aria-labelledby="settingsTitle">
          <div className="panel-head">
            <div>
              <h2 className="panel-title" id="settingsTitle">{visibleTab.label}</h2>
              <div className="panel-meta">{visibleTab.meta}</div>
            </div>
            <div className="top-actions">
              <button
                type="button"
                className="btn primary"
                onClick={handleSave}
                disabled={busy}
                aria-busy={busy}
              >
                {busy ? "保存中…" : "保存更改"}
              </button>
            </div>
          </div>

          <div
            className={`panel-body${leaving ? " is-leaving" : ""}`}
            id="settingsContent"
            role="tabpanel"
            aria-labelledby={`tab-${activeTab}`}
          >
            {activeTab === "brand" && (
              <BrandPanel settings={settings} update={update} />
            )}
            {activeTab === "providers" && (
              <ProvidersPanel
                settings={settings}
                update={update}
                showKeys={showKeys}
                onToggleKey={toggleKeyVisibility}
                busy={busy}
                onCheck={handleCheck}
              />
            )}
            {activeTab === "data" && (
              <DataPanel settings={settings} update={update} busy={busy} onCheck={handleCheck} />
            )}
            {activeTab === "export" && (
              <ExportPanel settings={settings} update={update} busy={busy} onCheck={handleCheck} />
            )}

            {resolved ? (
              <div className="error-guide-card" style={{ borderColor: "color-mix(in oklch, var(--success) 38%, var(--border))", background: "color-mix(in oklch, var(--success) 8%, var(--surface-2))" }}>
                <h4>解析后的 Provider（已掩码）</h4>
                <pre>{JSON.stringify(resolved, null, 2)}</pre>
              </div>
            ) : null}
          </div>
        </article>
      </section>
    </div>
  );
}

/* ---------- BrandProfile ---------- */
function BrandPanel({
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
            className="field"
            value={b.name}
            style={{ width: "100%" }}
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
            className="field"
            style={{ width: "100%" }}
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

/* ---------- AI Provider ---------- */
function ProvidersPanel({
  settings,
  update,
  showKeys,
  onToggleKey,
  busy,
  onCheck,
}: {
  settings: SettingsConfig;
  update: (patch: Partial<SettingsConfig>) => void;
  showKeys: Record<string, boolean>;
  onToggleKey: (field: string) => void;
  busy: boolean;
  onCheck: () => void;
}) {
  const llm = settings.llm;
  const asr = settings.asr;
  const tts = settings.tts;
  return (
    <>
      <div className="form-row">
        <label htmlFor="aiProvider">AI Provider</label>
        <select
          id="aiProvider"
          className="select"
          value={llm.provider}
          onChange={(e) =>
            update({ llm: { ...llm, provider: e.target.value as SettingsConfig["llm"]["provider"] } })
          }
        >
          <option value="cloud">cloud</option>
          <option value="openai-compatible">openai-compatible</option>
          <option value="ollama">ollama</option>
        </select>
      </div>
      <div className="form-row">
        <label htmlFor="aiModel">模型</label>
        <input
          id="aiModel"
          className="field"
          style={{ width: "100%" }}
          value={llm.model}
          onChange={(e) => update({ llm: { ...llm, model: e.target.value } })}
        />
      </div>

      <KeyField
        id="aiApiKey"
        label="API Key"
        value={llm.apiKey}
        visible={!!showKeys.aiApiKey}
        onToggle={() => onToggleKey("aiApiKey")}
        onChange={(v) => update({ llm: { ...llm, apiKey: v } })}
      />
      <div className="form-row">
        <label htmlFor="aiBaseUrl">Base URL</label>
        <input
          id="aiBaseUrl"
          className="field"
          style={{ width: "100%" }}
          value={llm.baseUrl}
          onChange={(e) => update({ llm: { ...llm, baseUrl: e.target.value } })}
        />
      </div>
      <div className="form-row">
        <label htmlFor="aiCostPer1k">费用 / 1k tokens</label>
        <input
          id="aiCostPer1k"
          className="field"
          value={llm.costPer1k}
          onChange={(e) => update({ llm: { ...llm, costPer1k: e.target.value } })}
        />
      </div>

      {/* 采样参数 */}
      <div className="form-row">
        <label htmlFor="temperature">Temperature</label>
        <input
          id="temperature"
          className="field"
          type="number"
          step="0.1"
          min="0"
          max="2"
          value={llm.sampling.temperature}
          onChange={(e) =>
            update({
              llm: { ...llm, sampling: { ...llm.sampling, temperature: Number(e.target.value) } },
            })
          }
        />
      </div>
      <div className="form-row">
        <label htmlFor="topP">Top P</label>
        <input
          id="topP"
          className="field"
          type="number"
          step="0.05"
          min="0"
          max="1"
          value={llm.sampling.topP}
          onChange={(e) =>
            update({ llm: { ...llm, sampling: { ...llm.sampling, topP: Number(e.target.value) } } })
          }
        />
      </div>
      <div className="form-row">
        <label htmlFor="maxTokens">Max Tokens</label>
        <input
          id="maxTokens"
          className="field"
          type="number"
          step="1"
          min="1"
          value={llm.sampling.maxTokens}
          onChange={(e) =>
            update({
              llm: { ...llm, sampling: { ...llm.sampling, maxTokens: Number(e.target.value) } },
            })
          }
        />
      </div>

      <div className="form-row">
        <label htmlFor="asrProvider">ASR Provider</label>
        <select
          id="asrProvider"
          className="select"
          value={asr.provider}
          onChange={(e) =>
            update({ asr: { ...asr, provider: e.target.value as SettingsConfig["asr"]["provider"] } })
          }
        >
          <option value="local">local</option>
          <option value="cloud">cloud</option>
        </select>
      </div>
      <KeyField
        id="asrApiKey"
        label="ASR API Key"
        value={asr.apiKey}
        visible={!!showKeys.asrApiKey}
        onToggle={() => onToggleKey("asrApiKey")}
        onChange={(v) => update({ asr: { ...asr, apiKey: v } })}
      />
      <div className="form-row">
        <label htmlFor="asrBaseUrl">ASR Base URL</label>
        <input
          id="asrBaseUrl"
          className="field"
          style={{ width: "100%" }}
          value={asr.baseUrl}
          onChange={(e) => update({ asr: { ...asr, baseUrl: e.target.value } })}
        />
      </div>

      <div className="form-row">
        <label htmlFor="ttsProvider">TTS Provider</label>
        <select
          id="ttsProvider"
          className="select"
          value={tts.provider}
          onChange={(e) =>
            update({ tts: { ...tts, provider: e.target.value as SettingsConfig["tts"]["provider"] } })
          }
        >
          <option value="local">local</option>
          <option value="cloud">cloud</option>
        </select>
      </div>
      <KeyField
        id="ttsApiKey"
        label="TTS API Key"
        value={tts.apiKey}
        visible={!!showKeys.ttsApiKey}
        onToggle={() => onToggleKey("ttsApiKey")}
        onChange={(v) => update({ tts: { ...tts, apiKey: v } })}
      />
      <div className="form-row">
        <label htmlFor="ttsBaseUrl">TTS Base URL</label>
        <input
          id="ttsBaseUrl"
          className="field"
          style={{ width: "100%" }}
          value={tts.baseUrl}
          onChange={(e) => update({ tts: { ...tts, baseUrl: e.target.value } })}
        />
      </div>
      <div className="form-row">
        <label htmlFor="ttsModel">TTS 模型</label>
        <input
          id="ttsModel"
          className="field"
          value={tts.model}
          onChange={(e) => update({ tts: { ...tts, model: e.target.value } })}
        />
      </div>

      <div className="empty">
        <h2>检查配置</h2>
        <p>默认文本模型：STEPFUN step-3.7；语音：StepAudio / Edge TTS。每次任务开始前展示模型、预计费用与上传范围。</p>
        <button type="button" className="btn" onClick={onCheck} disabled={busy} aria-busy={busy}>
          检查当前配置
        </button>
      </div>
    </>
  );
}

/* ---------- 数据与存储 ---------- */
function DataPanel({
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
  return (
    <>
      <div className="form-row">
        <label htmlFor="workspaceDefaultPath">项目默认文件夹</label>
        <div>
          <input
            id="workspaceDefaultPath"
            className="field"
            style={{ width: "100%" }}
            value={settings.workspace.defaultPath}
            placeholder="~/STEPWORK (STEPWORK_HOME)"
            onChange={(e) => update({ workspace: { ...settings.workspace, defaultPath: e.target.value } })}
          />
          <p className="form-help">本地项目空间 / STEPWORK_HOME</p>
        </div>
      </div>
      <div className="form-row">
        <label htmlFor="retentionDays">日志保留天数</label>
        <input
          id="retentionDays"
          className="field"
          type="number"
          min="1"
          value={d.retentionDays}
          onChange={(e) => update({ data: { ...d, retentionDays: Number(e.target.value) } })}
        />
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
          className="field"
          style={{ width: "100%" }}
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
function ExportPanel({
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
function KeyField({
  id,
  label,
  value,
  visible,
  onToggle,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  visible: boolean;
  onToggle: () => void;
  onChange: (v: string) => void;
}) {
  return (
    <div className="form-row">
      <label htmlFor={id}>{label}</label>
      <div className="password-wrap">
        <input
          id={id}
          className="field"
          type={visible ? "text" : "password"}
          autoComplete="off"
          value={value}
          placeholder="••••••••"
          onChange={(e) => onChange(e.target.value)}
        />
        <button
          type="button"
          className="pw-toggle"
          aria-label={visible ? "隐藏密钥" : "显示密钥"}
          aria-pressed={visible}
          onClick={onToggle}
        >
          {visible ? "隐藏" : "显示"}
        </button>
      </div>
    </div>
  );
}

function toggleInList(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}
