/**
 * SET.3 — SettingsView
 * 6 个 Tab（对齐 Prototype/settings.html + Tranche 2）：
 * BrandProfile / 品牌档案 / 工作区 / AI Provider / 数据与存储 / 导入与导出。
 * 所有字段绑定 useSettingsStore。API Key 仅以 password 控件呈现，绝不回显明文。
 *
 * Tranche 2：
 * - 品牌档案：多 BrandProfile 管理（列表/新建/编辑，含内容支柱与禁用表达标签输入），
 *   经 ListBrandProfiles / CreateBrandProfile / UpdateBrandProfile 命令落库
 * - 工作区：Workspace 管理（列表/新建/重命名/归档），
 *   经 ListWorkspaces / CreateWorkspace / RenameWorkspace / ArchiveWorkspace 命令
 */
import { useMemo, useState, useEffect, useCallback } from "react";
import { useSettingsStore } from "@/stores/useSettingsStore";
import type { CleanupMode, SettingsConfig } from "@/stores/useSettingsStore";
import {
  buildEnvelope,
  dispatchCommand,
  getConfig,
  getWorkspaceId,
  setWorkspaceId,
  updateConfig,
} from "@/lib/tauri";
import { TagListInput } from "@/components/TagListInput";
import type {
  BrandProfile,
  CreateBrandProfilePayload,
  UpdateBrandProfilePayload,
  WorkspaceRow,
} from "@/lib/types";

import "@/styles/settings.css";

/** 主代理在 tauri.ts 提供的返回类型（此处标注，供调用点复用） */
type ConfigResult = { ok: boolean; config?: unknown; resolved?: unknown; error?: string };

type TabKey =
  | "brand"
  | "brandProfiles"
  | "workspaces"
  | "providers"
  | "data"
  | "export";

const TABS: { key: TabKey; label: string; meta: string }[] = [
  { key: "brand", label: "BrandProfile", meta: "用于约束原创角度、脚本语气与事实表达" },
  { key: "brandProfiles", label: "品牌档案", meta: "多品牌档案：定位、受众、语气、内容支柱与禁用表达" },
  { key: "workspaces", label: "工作区", meta: "管理工作区：新建、重命名与归档" },
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
  // 无连接时 getConfig() 快速返回本地数据，不阻塞。
  useEffect(() => {
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
            {activeTab === "brandProfiles" && <BrandProfilesPanel />}
            {activeTab === "workspaces" && <WorkspacesPanel />}
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

/* ---------- 品牌档案（Tranche 2 · PRD-BRD-001/002） ---------- */

/** BrandProfile 编辑表单模型 */
interface BrandProfileForm {
  name: string;
  positioning: string;
  audience: string;
  tone: string;
  contentPillars: string[];
  bannedExpressions: string[];
}

function emptyBrandForm(): BrandProfileForm {
  return {
    name: "",
    positioning: "",
    audience: "",
    tone: "",
    contentPillars: [],
    bannedExpressions: [],
  };
}

function profileToForm(p: BrandProfile): BrandProfileForm {
  return {
    name: p.name ?? "",
    positioning: p.positioning ?? "",
    audience: p.audience ?? "",
    tone: p.tone ?? "",
    contentPillars: Array.isArray(p.contentPillars) ? p.contentPillars : [],
    bannedExpressions: Array.isArray(p.bannedExpressions) ? p.bannedExpressions : [],
  };
}

function BrandProfilesPanel() {
  const [profiles, setProfiles] = useState<BrandProfile[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** 正在编辑的档案 id；null = 新建；undefined = 未打开表单 */
  const [editingId, setEditingId] = useState<string | null | undefined>(undefined);
  const [form, setForm] = useState<BrandProfileForm>(emptyBrandForm());
  // PRD-BRD-003：历史脚本（风格参考）
  const [scriptsFor, setScriptsFor] = useState<string | null>(null);
  const [scripts, setScripts] = useState<
    { id: string; title: string; content: string }[]
  >([]);
  const [scriptTitle, setScriptTitle] = useState("");
  const [scriptBody, setScriptBody] = useState("");
  const [scriptKeyword, setScriptKeyword] = useState("");
  const [scriptBusy, setScriptBusy] = useState(false);

  async function loadScripts(profileId: string, keyword = "") {
    const env = buildEnvelope("ListBrandScripts", getWorkspaceId(), null, {
      profileId,
      ...(keyword ? { keyword } : {}),
    });
    const res = await dispatchCommand(env);
    if (res.ok) {
      const detail = (res.detail ?? {}) as {
        scripts?: { id: string; title: string; content: string }[];
      };
      setScripts(detail.scripts ?? []);
    }
  }

  async function openScripts(profileId: string) {
    if (scriptsFor === profileId) {
      setScriptsFor(null);
      return;
    }
    setScriptsFor(profileId);
    setScriptKeyword("");
    await loadScripts(profileId);
  }

  async function importScript(profileId: string) {
    setScriptBusy(true);
    try {
      const env = buildEnvelope("ImportBrandScript", getWorkspaceId(), null, {
        profileId,
        title: scriptTitle,
        content: scriptBody,
        source: "manual",
      });
      const res = await dispatchCommand(env);
      if (!res.ok) {
        setError(res.error ?? "导入历史脚本失败");
        return;
      }
      setScriptTitle("");
      setScriptBody("");
      await loadScripts(profileId, scriptKeyword);
    } finally {
      setScriptBusy(false);
    }
  }

  async function deleteScript(scriptId: string, profileId: string) {
    const env = buildEnvelope("DeleteBrandScript", getWorkspaceId(), null, {
      scriptId,
    });
    const res = await dispatchCommand(env);
    if (!res.ok) setError(res.error ?? "删除失败");
    await loadScripts(profileId, scriptKeyword);
  }

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const env = buildEnvelope("ListBrandProfiles", getWorkspaceId(), null, {});
      const res = await dispatchCommand(env);
      if (!res.ok) throw new Error(res.error ?? "LIST_BRAND_PROFILES_FAILED");
      const detail = (res.detail ?? {}) as { profiles?: BrandProfile[] };
      setProfiles(detail.profiles ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function startCreate() {
    setEditingId(null);
    setForm(emptyBrandForm());
  }

  function startEdit(p: BrandProfile) {
    setEditingId(p.id);
    setForm(profileToForm(p));
  }

  async function handleSubmit() {
    if (!form.name.trim()) {
      setError("请填写档案名称");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const base: CreateBrandProfilePayload = {
        name: form.name.trim(),
        positioning: form.positioning,
        audience: form.audience,
        tone: form.tone,
        contentPillars: form.contentPillars,
        bannedExpressions: form.bannedExpressions,
      };
      const env =
        editingId == null
          ? buildEnvelope("CreateBrandProfile", getWorkspaceId(), null, base)
          : buildEnvelope("UpdateBrandProfile", getWorkspaceId(), null, {
              profileId: editingId,
              ...base,
            } satisfies UpdateBrandProfilePayload);
      const res = await dispatchCommand(env);
      if (!res.ok) throw new Error(res.error ?? "SAVE_BRAND_PROFILE_FAILED");
      setEditingId(undefined);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      {error && (
        <p className="error-text" style={{ color: "var(--danger)", marginBottom: 12 }}>
          {error}
        </p>
      )}

      {editingId === undefined ? (
        <>
          {loading && <p className="panel-meta">加载品牌档案…</p>}
          {!loading && profiles.length === 0 && (
            <p className="panel-meta">尚无品牌档案。新建档案后可在项目上关联，约束角度与脚本生成。</p>
          )}
          {profiles.length > 0 && (
            <div className="task-list" style={{ marginBottom: 12 }}>
              {profiles.map((p) => (
                <div className="task-item" key={p.id}>
                  <div className="task-top">
                    <div>
                      <div className="task-name">{p.name}</div>
                      <div className="row-sub">
                        {p.positioning || "（未填写定位）"}
                        {p.audience ? ` · 受众：${p.audience}` : ""}
                      </div>
                    </div>
                    <div className="inline-actions">
                      <button
                        className="btn small ghost"
                        type="button"
                        onClick={() => startEdit(p)}
                      >
                        编辑
                      </button>
                      {/* PRD-BRD-003：导入历史脚本作为风格参考 */}
                      <button
                        className="btn small ghost"
                        type="button"
                        onClick={() => void openScripts(p.id)}
                      >
                        历史脚本
                      </button>
                    </div>
                  </div>
                  {scriptsFor === p.id && (
                    <div className="section-gap">
                      <p className="panel-meta" style={{ marginTop: 0 }}>
                        导入的历史脚本会作为**风格范文**注入生成提示词
                        （最多 3 篇，各截取开头部分）。
                      </p>
                      <div className="form-group">
                        <input
                          className="field"
                          placeholder="脚本标题（可空）"
                          value={scriptTitle}
                          onChange={(e) => setScriptTitle(e.target.value)}
                        />
                        <textarea
                          className="field"
                          rows={4}
                          placeholder="粘贴历史脚本正文"
                          value={scriptBody}
                          onChange={(e) => setScriptBody(e.target.value)}
                          style={{ width: "100%", marginTop: 6 }}
                        />
                        <div className="inline-actions" style={{ marginTop: 6 }}>
                          <button
                            className="btn small primary"
                            type="button"
                            disabled={!scriptBody.trim() || scriptBusy}
                            onClick={() => void importScript(p.id)}
                          >
                            {scriptBusy ? "导入中…" : "导入为范文"}
                          </button>
                          <input
                            className="field"
                            placeholder="检索历史脚本"
                            value={scriptKeyword}
                            onChange={(e) => {
                              setScriptKeyword(e.target.value);
                              void loadScripts(p.id, e.target.value);
                            }}
                            style={{ maxWidth: 200 }}
                          />
                        </div>
                      </div>
                      {scripts.length === 0 ? (
                        <p className="panel-meta">尚无历史脚本。</p>
                      ) : (
                        <ul className="report-list">
                          {scripts.map((sc) => (
                            <li key={sc.id}>
                              <strong>{sc.title || "无标题"}</strong>
                              {" · "}
                              {sc.content.slice(0, 40)}
                              {sc.content.length > 40 ? "…" : ""}
                              <button
                                className="btn small ghost"
                                type="button"
                                style={{ marginLeft: 8 }}
                                onClick={() => void deleteScript(sc.id, p.id)}
                              >
                                删除
                              </button>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  )}
                  {(p.contentPillars?.length > 0 || p.bannedExpressions?.length > 0) && (
                    <p className="panel-meta" style={{ margin: "6px 0 0" }}>
                      {p.contentPillars?.length > 0
                        ? `支柱：${p.contentPillars.join(" / ")}`
                        : ""}
                      {p.contentPillars?.length > 0 && p.bannedExpressions?.length > 0
                        ? " · "
                        : ""}
                      {p.bannedExpressions?.length > 0
                        ? `禁用：${p.bannedExpressions.join(" / ")}`
                        : ""}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
          <div className="inline-actions">
            <button className="btn small ghost" type="button" onClick={() => void load()} disabled={loading}>
              刷新
            </button>
            <button className="btn small primary" type="button" onClick={startCreate}>
              新建档案
            </button>
          </div>
        </>
      ) : (
        <>
          <div className="form-row">
            <label htmlFor="bpName">档案名称</label>
            <div>
              <input
                id="bpName"
                className="field"
                style={{ width: "100%" }}
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="如：科技实测 · 克制判断"
              />
            </div>
          </div>
          <div className="form-row">
            <label htmlFor="bpPositioning">定位</label>
            <div>
              <input
                id="bpPositioning"
                className="field"
                style={{ width: "100%" }}
                value={form.positioning}
                onChange={(e) => setForm({ ...form, positioning: e.target.value })}
                placeholder="账号的内容定位"
              />
            </div>
          </div>
          <div className="form-row">
            <label htmlFor="bpAudience">核心受众</label>
            <div>
              <input
                id="bpAudience"
                className="field"
                style={{ width: "100%" }}
                value={form.audience}
                onChange={(e) => setForm({ ...form, audience: e.target.value })}
              />
            </div>
          </div>
          <div className="form-row">
            <label htmlFor="bpTone">语气 / 表达原则</label>
            <div>
              <textarea
                id="bpTone"
                className="textarea"
                value={form.tone}
                onChange={(e) => setForm({ ...form, tone: e.target.value })}
              />
            </div>
          </div>
          <div className="form-row">
            <label htmlFor="bpPillars">内容支柱</label>
            <TagListInput
              id="bpPillars"
              value={form.contentPillars}
              onChange={(next) => setForm({ ...form, contentPillars: next })}
              placeholder="输入内容支柱后回车添加"
              disabled={saving}
            />
          </div>
          <div className="form-row">
            <label htmlFor="bpBanned">禁用表达</label>
            <TagListInput
              id="bpBanned"
              value={form.bannedExpressions}
              onChange={(next) => setForm({ ...form, bannedExpressions: next })}
              placeholder="输入禁用表达后回车添加"
              disabled={saving}
            />
          </div>
          <div className="inline-actions">
            <button
              className="btn small ghost"
              type="button"
              onClick={() => setEditingId(undefined)}
              disabled={saving}
            >
              取消
            </button>
            <button
              className="btn small primary"
              type="button"
              onClick={() => void handleSubmit()}
              disabled={saving}
              aria-busy={saving}
            >
              {saving ? "保存中…" : editingId == null ? "创建档案" : "保存修改"}
            </button>
          </div>
        </>
      )}
    </>
  );
}

/* ---------- 工作区管理（Tranche 2 · PRD-WS-001） ---------- */
function WorkspacesPanel() {
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
        <div className="task-list" style={{ marginBottom: 12 }}>
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
      <div className="inline-actions" style={{ marginTop: 12 }}>
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
            <span className="panel-meta" role="status" style={{ alignSelf: "center" }}>
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
