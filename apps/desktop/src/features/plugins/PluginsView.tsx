/**
 * 插件视图（W8）
 * 列出已注册插件（ListPlugins），每个插件展示 id/name/version/status/enabled
 * + Enable / Disable 按钮（EnablePlugin / DisablePlugin）。
 * 空态：暂无已注册插件。示例插件在 plugins/examples/ 下。
 */

import { useEffect, useState } from "react";
import { buildEnvelope, dispatchCommand, getWorkspaceId } from "@/lib/tauri";
import type { CommandResult } from "@/lib/types";

interface PluginEntry {
  id: string;
  name: string;
  version: string;
  status: string;
  enabled: boolean;
}

export function PluginsView() {
  const [plugins, setPlugins] = useState<PluginEntry[]>([]);
  const [isBusy, setIsBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [togglingId, setTogglingId] = useState<string | null>(null);

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
        const d = (res.detail ?? {}) as { plugins?: PluginEntry[] };
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

  async function toggle(plugin: PluginEntry, enable: boolean) {
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

  const isEmpty = !isBusy && plugins.length === 0 && !error;

  return (
    <section className="feature-view" data-od-id="plugins-view">
      <header className="feature-head">
        <h1>插件</h1>
        <p className="feature-sub">查看与管理已注册插件</p>
      </header>

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
          {plugins.map((p) => (
            <li key={p.id} className="plugin-item" data-od-id={`plugin-${p.id}`}>
              <div className="plugin-head">
                <span className="plugin-name">{p.name}</span>
                <span className="status-badge" data-status={p.status}>
                  {p.status}
                </span>
              </div>
              <p className="plugin-meta">
                id {p.id} · v{p.version} ·{" "}
                {p.enabled ? "已启用" : "已禁用"}
              </p>
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
          ))}
        </ul>
      )}
    </section>
  );
}
