/**
 * SET.3 — SettingsView（仅剩 Tab 骨架）
 *
 * 六个 Panel 已各自独立成文件（``./panels/``）。此前它们都是同一个 1430 行
 * 文件里的函数 —— 半抽半留比全部内联更糟，因为读代码的人不知道该去哪找。
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
import { useEffect, useMemo, useState } from "react";
import { useSettingsStore } from "@/stores/useSettingsStore";
import type { SettingsConfig } from "@/stores/useSettingsStore";
import { getConfig, updateConfig } from "@/lib/tauri";
import { BrandPanel } from "./panels/BrandPanel";
import { BrandProfilesPanel } from "./panels/BrandProfilesPanel";
import { WorkspacesPanel } from "./panels/WorkspacesPanel";
import { ProvidersPanel } from "./panels/ProvidersPanel";
import { DataPanel } from "./panels/DataPanel";
import { ExportPanel } from "./panels/ExportPanel";

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
