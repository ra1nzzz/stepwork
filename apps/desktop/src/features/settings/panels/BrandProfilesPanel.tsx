/** 品牌档案 Tab：多 BrandProfile 管理（列表/新建/编辑）。 */
import { useCallback, useEffect, useState } from "react";
import { buildEnvelope, dispatchCommand, getWorkspaceId } from "@/lib/tauri";
import { TagListInput } from "@/components/TagListInput";
import type {
  BrandProfile,
  CreateBrandProfilePayload,
  UpdateBrandProfilePayload,
} from "@/lib/types";
import { emptyBrandForm, profileToForm, type BrandProfileForm } from "./shared";



export function BrandProfilesPanel() {
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
            <div className="task-list stack-md">
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
                      <p className="panel-meta flush-top">
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
                        <div className="inline-actions gap-top-sm">
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
                                className="btn small ghost gap-left-sm"
                                type="button" 
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
                    <p className="panel-meta hint-inline">
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
                className="field w-full" 
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
                className="field w-full" 
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
                className="field w-full" 
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
