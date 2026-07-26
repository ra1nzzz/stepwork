/**
 * 发布视图（Tranche 2 · PRD-PUB-001/002）
 *
 * 数据流：
 *   1. 项目选择：ListProjects 加载列表，默认复用 useViewStore.selectedProjectId
 *   2. 变体表单（platform/title/body/tags）→ CreatePlatformVariant（主稿绝不修改）
 *   3. 变体列表：ListPlatformVariants；每条变体可 导出 → ExportBundle
 *   4. 导出成功后显示「打开目录」→ @tauri-apps/plugin-opener openPath()
 *
 * 渲染流程若已产出 video_draft（useRenderStore.videoVersionId），
 * 创建变体时自动携带 videoVersionId，导出包才会包含 video.mp4。
 */

import { useCallback, useEffect, useState } from "react";
import { buildEnvelope, dispatchCommand, getWorkspaceId } from "@/lib/tauri";
import { openLocalPath } from "@/lib/shell";
import { useViewStore } from "@/stores/useViewStore";
import { useRenderStore } from "@/stores/useRenderStore";
import { TagListInput } from "@/components/TagListInput";
import type {
  CreatePlatformVariantPayload,
  PlatformVariant,
  PublishPlatform,
} from "@/lib/types";

interface ProjectOption {
  id: string;
  title: string;
}

/** tags 列可能是 JSON string（DB 原样）或已解析数组，统一归一化 */
function normalizeTags(tags: unknown): string[] {
  if (Array.isArray(tags)) return tags.map(String);
  if (typeof tags === "string") {
    try {
      const parsed = JSON.parse(tags);
      return Array.isArray(parsed) ? parsed.map(String) : [];
    } catch {
      return tags ? [tags] : [];
    }
  }
  return [];
}

function platformLabel(p: string): string {
  return p === "douyin" ? "抖音" : "通用";
}

export function PublishView() {
  const selectedProjectId = useViewStore((s) => s.selectedProjectId);
  const setSelectedProjectId = useViewStore((s) => s.setSelectedProjectId);
  const videoVersionId = useRenderStore((s) => s.videoVersionId);

  const [projects, setProjects] = useState<ProjectOption[]>([]);
  const [projectId, setProjectId] = useState<string | null>(selectedProjectId);
  const [variants, setVariants] = useState<PlatformVariant[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // 变体表单
  const [platform, setPlatform] = useState<PublishPlatform>("douyin");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);

  // 导出状态：variantId → bundle_path（导出成功后可打开目录）
  const [bundlePaths, setBundlePaths] = useState<Record<string, string>>({});
  const [exportingId, setExportingId] = useState<string | null>(null);
  // PRD-PUB-004：发布授权申请
  const [authorizingId, setAuthorizingId] = useState<string | null>(null);
  const [authNotice, setAuthNotice] = useState<Record<string, string>>({});

  // 加载项目列表（进入发布页时）
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const env = buildEnvelope("ListProjects", getWorkspaceId(), null, {});
        const res = await dispatchCommand(env);
        if (cancelled || !res.ok) return;
        const detail = (res.detail ?? {}) as {
          projects?: { id: string; title: string }[];
        };
        setProjects(
          (detail.projects ?? []).map((p) => ({ id: p.id, title: p.title })),
        );
      } catch {
        /* 后端未连接：保持空列表 */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const loadVariants = useCallback(async (pid: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const env = buildEnvelope("ListPlatformVariants", getWorkspaceId(), pid, {
        projectId: pid,
      });
      const res = await dispatchCommand(env);
      if (!res.ok) throw new Error(res.error ?? "LIST_VARIANTS_FAILED");
      const detail = (res.detail ?? {}) as { variants?: PlatformVariant[] };
      setVariants(detail.variants ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setIsLoading(false);
    }
  }, []);

  // 项目切换时重载变体列表
  useEffect(() => {
    if (projectId) void loadVariants(projectId);
    else setVariants([]);
  }, [projectId, loadVariants]);

  function handleProjectChange(id: string) {
    setProjectId(id || null);
    const proj = projects.find((p) => p.id === id);
    if (proj) setSelectedProjectId(proj.id, proj.title);
  }

  async function handleCreate() {
    if (!projectId || creating) return;
    if (!title.trim()) {
      setError("请填写变体标题");
      return;
    }
    setCreating(true);
    setError(null);
    setNotice(null);
    try {
      const payload: CreatePlatformVariantPayload = {
        projectId,
        platform,
        title: title.trim(),
        body,
        tags,
      };
      if (videoVersionId) payload.videoVersionId = videoVersionId;
      const env = buildEnvelope(
        "CreatePlatformVariant",
        getWorkspaceId(),
        projectId,
        payload,
      );
      const res = await dispatchCommand(env);
      if (!res.ok) throw new Error(res.error ?? "CREATE_VARIANT_FAILED");
      setNotice(`已创建 ${platformLabel(platform)} 变体`);
      setTitle("");
      setBody("");
      setTags([]);
      await loadVariants(projectId);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  }

  async function handleExport(variantId: string) {
    if (exportingId) return;
    setExportingId(variantId);
    setError(null);
    setNotice(null);
    try {
      const env = buildEnvelope("ExportBundle", getWorkspaceId(), projectId, {
        variantId,
      });
      const res = await dispatchCommand(env);
      if (!res.ok) throw new Error(res.error ?? "EXPORT_BUNDLE_FAILED");
      const detail = (res.detail ?? {}) as { bundle_path?: string };
      const path = detail.bundle_path;
      if (!path) throw new Error("EXPORT_NO_BUNDLE_PATH");
      setBundlePaths((prev) => ({ ...prev, [variantId]: path }));
      setNotice(`已导出到：${path}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setExportingId(null);
    }
  }

  /**
   * PRD-PUB-004「一次性发布授权」：授权与账号、内容哈希、插件版本绑定，
   * 生成一条待审批请求（在「任务 → 审批中心」处理）。
   * 批准本身不发布——PRD §10.6「默认不过度自动化」。
   */
  async function requestAuthorization(variantId: string) {
    setAuthorizingId(variantId);
    try {
      const env = buildEnvelope(
        "RequestPublishAuthorization",
        getWorkspaceId(),
        projectId,
        { variantId },
      );
      const res = await dispatchCommand(env);
      setAuthNotice((prev) => ({
        ...prev,
        [variantId]: res.ok
          ? "已创建发布授权请求，请到「任务 → 审批中心」确认"
          : (res.error ?? "申请失败"),
      }));
    } catch (e) {
      setAuthNotice((prev) => ({
        ...prev,
        [variantId]: e instanceof Error ? e.message : String(e),
      }));
    } finally {
      setAuthorizingId(null);
    }
  }

  async function handleOpenDir(path: string) {
    try {
      await openLocalPath(path);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <>
      <section className="page-head" data-od-id="publish-heading">
        <div>
          <p className="eyebrow">PUBLISH VARIANTS</p>
          <h1>为每个平台生成发布变体</h1>
          <p className="page-subtitle">
            变体是主稿的平台化副本（标题/正文/标签），主稿版本绝不被修改。
            导出包包含视频、封面、文案与全量元数据。
          </p>
        </div>
        <span className={`status ${error ? "danger" : isLoading ? "ai" : "success"}`}>
          {error ? "出错" : isLoading ? "加载中" : `${variants.length} 个变体`}
        </span>
      </section>

      <section className="layout-main section-gap" data-od-id="publish-layout">
        {/* 左：变体表单 */}
        <article className="panel" data-od-id="variant-form-panel">
          <div className="panel-head">
            <div>
              <h2 className="panel-title">新建平台变体</h2>
              <div className="panel-meta">
                {videoVersionId
                  ? `将关联视频草稿 ${videoVersionId.slice(0, 8)}…`
                  : "尚无视频草稿，导出包将不含 video.mp4"}
              </div>
            </div>
          </div>
          <div className="panel-body">
            <div className="form-group" style={{ marginBottom: 12 }}>
              <label htmlFor="publishProject">项目</label>
              <select
                id="publishProject"
                className="select"
                value={projectId ?? ""}
                onChange={(e) => handleProjectChange(e.target.value)}
              >
                <option value="">选择项目…</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.title}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-group" style={{ marginBottom: 12 }}>
              <label htmlFor="variantPlatform">平台</label>
              <select
                id="variantPlatform"
                className="select"
                value={platform}
                onChange={(e) => setPlatform(e.target.value as PublishPlatform)}
              >
                <option value="douyin">抖音</option>
                <option value="generic">通用</option>
              </select>
            </div>
            <div className="form-group" style={{ marginBottom: 12 }}>
              <label htmlFor="variantTitle">标题</label>
              <input
                id="variantTitle"
                className="field"
                style={{ width: "100%" }}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="平台发布标题"
              />
            </div>
            <div className="form-group" style={{ marginBottom: 12 }}>
              <label htmlFor="variantBody">正文</label>
              <textarea
                id="variantBody"
                className="field"
                rows={6}
                style={{ width: "100%", resize: "vertical", fontFamily: "inherit" }}
                value={body}
                onChange={(e) => setBody(e.target.value)}
                placeholder="发布文案正文"
              />
            </div>
            <div className="form-group" style={{ marginBottom: 12 }}>
              <label htmlFor="variantTags">标签</label>
              <TagListInput
                id="variantTags"
                value={tags}
                onChange={setTags}
                placeholder="输入标签后回车添加"
              />
            </div>
            <button
              className="btn primary"
              type="button"
              style={{ width: "100%" }}
              onClick={() => void handleCreate()}
              disabled={!projectId || creating}
            >
              {creating ? "创建中…" : "创建变体"}
            </button>
          </div>
        </article>

        {/* 右：变体列表 */}
        <aside className="panel" data-od-id="variant-list-panel">
          <div className="panel-head">
            <div>
              <h2 className="panel-title">变体列表</h2>
              <div className="panel-meta">
                {projectId ? "每条变体可单独导出发布包" : "请先选择项目"}
              </div>
            </div>
            <button
              className="btn small ghost"
              type="button"
              onClick={() => projectId && void loadVariants(projectId)}
              disabled={!projectId || isLoading}
            >
              刷新
            </button>
          </div>
          <div className="panel-body">
            {error && (
              <p className="error-text section-gap" style={{ color: "var(--danger)" }}>
                {error}
              </p>
            )}
            {notice && (
              <p className="panel-meta section-gap" style={{ color: "var(--success)" }}>
                {notice}
              </p>
            )}
            {variants.length === 0 && !isLoading ? (
              <p className="panel-meta" style={{ margin: 0 }}>
                {projectId
                  ? "尚无变体。填写左侧表单创建第一个平台变体。"
                  : "选择项目后加载该项目的发布变体。"}
              </p>
            ) : (
              <div className="task-list">
                {variants.map((v) => {
                  const vTags = normalizeTags(v.tags);
                  const bundlePath = bundlePaths[v.id];
                  return (
                    <div className="task-item" key={v.id}>
                      <div className="task-top">
                        <div>
                          <div className="task-name">
                            {v.title || "（无标题）"}
                          </div>
                          <div className="row-sub">
                            {platformLabel(v.platform)}
                            {vTags.length > 0 ? ` · ${vTags.join(" / ")}` : ""}
                            {v.created_at ? ` · ${v.created_at.slice(0, 16)}` : ""}
                          </div>
                        </div>
                        <span className="status">{platformLabel(v.platform)}</span>
                      </div>
                      {v.body && (
                        <p className="panel-meta" style={{ margin: "6px 0 0" }}>
                          {v.body.slice(0, 120)}
                          {v.body.length > 120 ? "…" : ""}
                        </p>
                      )}
                      <div className="task-actions">
                        <button
                          className="btn small"
                          type="button"
                          onClick={() => void handleExport(v.id)}
                          disabled={exportingId !== null}
                        >
                          {exportingId === v.id ? "导出中…" : "导出"}
                        </button>
                        {bundlePath && (
                          <button
                            className="btn small ghost"
                            type="button"
                            onClick={() => void handleOpenDir(bundlePath)}
                          >
                            打开目录
                          </button>
                        )}
                        {/* PRD-PUB-004：申请一次性发布授权（与账号/内容/
                            插件版本绑定；批准后仍需你自行发布） */}
                        <button
                          className="btn small ghost"
                          type="button"
                          onClick={() => void requestAuthorization(v.id)}
                          disabled={authorizingId !== null}
                        >
                          {authorizingId === v.id ? "申请中…" : "申请发布授权"}
                        </button>
                      </div>
                      {authNotice[v.id] && (
                        <p className="panel-meta" style={{ margin: "6px 0 0" }}>
                          {authNotice[v.id]}
                        </p>
                      )}
                      {bundlePath && (
                        <p className="panel-meta mono" style={{ margin: "6px 0 0", fontSize: 11 }}>
                          {bundlePath}
                        </p>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </aside>
      </section>
    </>
  );
}
