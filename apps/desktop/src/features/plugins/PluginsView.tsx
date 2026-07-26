/**
 * 插件视图（W8 → Tranche 1 / T4）
 * 列出已注册插件（ListPlugins）。worker 返回行形如
 * {id, enabled, status, manifest: {name, version, permissions, ...}, installed_at, error_message}
 * —— name/version/permissions 都在 manifest 里（解析失败时 manifest 为 null，
 * status 为 'error'）。每个插件展示 manifest.permissions 权限列表（PRD-PLG-002）。
 *
 * 操作：
 * - Enable / Disable（EnablePlugin / DisablePlugin）
 * - 安装插件：dialog 选择含 manifest.json 的目录 → InstallPlugin {path} → 刷新列表
 */

import { useEffect, useState } from "react";
import { buildEnvelope, dispatchCommand, getWorkspaceId, isTauri } from "@/lib/tauri";
import type { CommandResult, InstalledPlugin, InstallPluginPayload } from "@/lib/types";

export function PluginsView() {
  const [plugins, setPlugins] = useState<InstalledPlugin[]>([]);
  const [isBusy, setIsBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [installing, setInstalling] = useState(false);
  // PRD-PLG-002：待确认的安装（读到 manifest 权限后、真正安装前）
  const [pendingInstall, setPendingInstall] = useState<{
    path: string;
    name: string;
    version: string;
    permissions: string[];
    alreadyInstalled: boolean;
  } | null>(null);
  const inTauri = isTauri();

  async function load() {
    setIsBusy(true);
    setError(null);
    try {
      const env = buildEnvelope("ListPlugins", getWorkspaceId(), null, {});
      const res: CommandResult = await dispatchCommand(env);
      if (!res.ok) {
        setError(res.error ?? "加载插件失败");
        setPlugins([]);
      } else {
        const d = (res.detail ?? {}) as { plugins?: InstalledPlugin[] };
        setPlugins(d.plugins ?? []);
      }
    } catch (e) {
      setError(String(e));
      setPlugins([]);
    } finally {
      setIsBusy(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function toggle(plugin: InstalledPlugin, enable: boolean) {
    setTogglingId(plugin.id);
    try {
      const commandType = enable ? "EnablePlugin" : "DisablePlugin";
      const env = buildEnvelope(commandType, getWorkspaceId(), null, {
        plugin_id: plugin.id,
      });
      const res: CommandResult = await dispatchCommand(env);
      if (res.ok) {
        setPlugins((prev) =>
          prev.map((p) =>
            p.id === plugin.id ? { ...p, enabled: enable } : p,
          ),
        );
      } else {
        setError(res.error ?? "切换失败");
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setTogglingId(null);
    }
  }

  /**
   * PRD-PLG-002「安装前显示所有权限」：选目录 → PreviewPluginManifest
   * 读取权限 → 展示确认卡 → 用户明确同意后才 InstallPlugin。
   * 此前是选目录直接安装，权限在装完之后才显示。
   */
  async function pickPluginForInstall() {
    if (!inTauri || installing) return;
    setError(null);
    try {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const dir = await open({
        directory: true,
        multiple: false,
        title: "选择包含 manifest.json 的插件目录",
      });
      if (!dir || typeof dir !== "string") return;
      const env = buildEnvelope("PreviewPluginManifest", getWorkspaceId(), null, {
        path: dir,
      });
      const res: CommandResult = await dispatchCommand(env);
      if (!res.ok) {
        setError(res.error ?? "读取插件 manifest 失败");
        return;
      }
      const detail = (res.detail ?? {}) as {
        manifest?: { id?: string; name?: string; version?: string };
        permissions?: string[];
        already_installed?: boolean;
      };
      setPendingInstall({
        path: dir,
        name: detail.manifest?.name ?? detail.manifest?.id ?? "未知插件",
        version: detail.manifest?.version ?? "-",
        permissions: detail.permissions ?? [],
        alreadyInstalled: Boolean(detail.already_installed),
      });
    } catch (e) {
      setError(String(e));
    }
  }

  /** 用户在权限确认卡上点「同意并安装」后才真正落库 */
  async function confirmInstall() {
    if (!pendingInstall || installing) return;
    setInstalling(true);
    setError(null);
    try {
      const payload: InstallPluginPayload = { path: pendingInstall.path };
      const env = buildEnvelope("InstallPlugin", getWorkspaceId(), null, payload);
      const res: CommandResult = await dispatchCommand(env);
      if (!res.ok) {
        setError(res.error ?? "安装插件失败");
        return;
      }
      setPendingInstall(null);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setInstalling(false);
    }
  }

  const isEmpty = !isBusy && plugins.length === 0 && !error;

  return (
    <section className="feature-view" data-od-id="plugins-view">
      <header className="feature-head">
        <h1>插件</h1>
        <p className="feature-sub">查看与管理已注册插件</p>
      </header>

      <div className="inline-actions" style={{ marginBottom: 16 }}>
        <button
          type="button"
          className="btn primary"
          disabled={!inTauri || installing}
          title={inTauri ? undefined : "安装插件需要在桌面应用中使用"}
          onClick={() => void pickPluginForInstall()}
          data-od-id="install-plugin"
        >
          {installing ? "安装中…" : "安装插件"}
        </button>
        <button
          type="button"
          className="btn ghost"
          disabled={isBusy}
          onClick={() => void load()}
        >
          刷新
        </button>
      </div>

      {/* PRD-PLG-002：安装前权限确认卡 —— 用户看到全部权限并明确同意后才安装 */}
      {pendingInstall && (
        <article className="panel" data-od-id="plugin-install-confirm">
          <div className="panel-head">
            <div>
              <h2 className="panel-title">确认安装插件</h2>
              <div className="panel-meta">
                {pendingInstall.name} · v{pendingInstall.version}
                {pendingInstall.alreadyInstalled && " · 将覆盖已安装的同 id 插件"}
              </div>
            </div>
          </div>
          <div className="panel-body">
            <p className="panel-meta" style={{ marginTop: 0 }}>
              该插件将获得以下权限：
            </p>
            {pendingInstall.permissions.length > 0 ? (
              <ul className="plugin-permissions">
                {pendingInstall.permissions.map((perm) => (
                  <li key={perm} className="permission-badge">
                    {perm}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="panel-meta">该插件未声明任何权限。</p>
            )}
            <p className="panel-meta mono" style={{ fontSize: 11 }}>
              {pendingInstall.path}
            </p>
            <div className="inline-actions">
              <button
                type="button"
                className="btn small primary"
                disabled={installing}
                onClick={() => void confirmInstall()}
              >
                {installing ? "安装中…" : "同意并安装"}
              </button>
              <button
                type="button"
                className="btn small ghost"
                disabled={installing}
                onClick={() => setPendingInstall(null)}
              >
                取消
              </button>
            </div>
          </div>
        </article>
      )}

      {isBusy && <p className="feature-sub">加载中…</p>}

      {error && (
        <p className="error-text" data-od-id="plugins-error">
          {error}
        </p>
      )}

      {isEmpty && (
        <div className="empty-state" data-od-id="plugins-empty">
          <p className="empty-title">暂无已注册插件。</p>
          <p className="empty-sub">示例插件在 plugins/examples/ 下。</p>
        </div>
      )}

      {plugins.length > 0 && (
        <ul className="plugin-list">
          {plugins.map((p) => {
            const name = p.manifest?.name ?? p.id;
            const version = p.manifest?.version ?? null;
            const permissions = p.manifest?.permissions ?? [];
            return (
              <li key={p.id} className="plugin-item" data-od-id={`plugin-${p.id}`}>
                <div className="plugin-head">
                  <span className="plugin-name">{name}</span>
                  <span className="status-badge" data-status={p.status}>
                    {p.status}
                  </span>
                </div>
                <p className="plugin-meta">
                  id {p.id}
                  {version ? ` · v${version}` : " · 版本未知"} ·{" "}
                  {p.enabled ? "已启用" : "已禁用"}
                </p>
                {p.error_message && (
                  <p className="error-text" style={{ color: "var(--danger)" }}>
                    {p.error_message}
                  </p>
                )}
                <div className="plugin-permissions" data-od-id={`plugin-perms-${p.id}`}>
                  <span className="panel-meta">权限：</span>
                  {permissions.length === 0 ? (
                    <span className="panel-meta">无声明权限</span>
                  ) : (
                    permissions.map((perm) => (
                      <span key={perm} className="status-badge mono" data-status="permission">
                        {perm}
                      </span>
                    ))
                  )}
                </div>
                <div className="plugin-actions">
                  <button
                    type="button"
                    className="btn"
                    disabled={p.enabled || togglingId === p.id}
                    onClick={() => void toggle(p, true)}
                  >
                    启用
                  </button>
                  <button
                    type="button"
                    className="btn ghost"
                    disabled={!p.enabled || togglingId === p.id}
                    onClick={() => void toggle(p, false)}
                  >
                    禁用
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
