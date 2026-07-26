import { useHealthStore } from "@/stores/useHealthStore";
import { useViewStore, type ViewId } from "@/stores/useViewStore";

interface NavItemDef {
  id: ViewId;
  label: string;
  icon: string;
}

/** Prototype index.html .nav 5 项 + 插件（PRD-PLG，恢复孤儿路由） */
const NAV_ITEMS: NavItemDef[] = [
  { id: "home", label: "首页", icon: "01" },
  { id: "projects", label: "项目", icon: "02" },
  { id: "create", label: "创作", icon: "03" },
  { id: "tasks", label: "任务", icon: "04" },
  { id: "settings", label: "设置", icon: "05" },
  { id: "plugins", label: "插件", icon: "06" },
];

function statusLabel(status: "ok" | "degraded" | "down" | null): string {
  if (status === "ok") return "运行正常";
  if (status === "degraded") return "性能下降";
  if (status === "down") return "已停止";
  return "连接中";
}

/**
 * 左侧导航栏：与 Prototype .sidebar 结构对齐
 * 6 项一级导航（首页/项目/创作/任务/设置/插件）
 */
export function Sidebar() {
  const health = useHealthStore((s) => s.health);
  const status = health?.status ?? null;
  const currentView = useViewStore((s) => s.currentView);
  const setView = useViewStore((s) => s.setView);

  return (
    <aside className="sidebar" data-od-id="primary-sidebar">
      <div className="brand" data-od-id="brand-home">
        <span className="brand-mark" aria-hidden="true" />
        STEPWORK
      </div>
      <p className="nav-label">内容工作区</p>
      <nav className="nav" aria-label="主导航" data-od-id="primary-navigation">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`nav-item${currentView === item.id ? " active" : ""}`}
            aria-current={currentView === item.id ? "page" : undefined}
            data-od-id={`nav-${item.id}`}
            onClick={() => setView(item.id)}
          >
            <span className="nav-icon" aria-hidden="true">
              {item.icon}
            </span>
            {item.label}
          </button>
        ))}
      </nav>
      <div className="sidebar-foot">
        <div className="engine-state" data-od-id="engine-status">
          <div className="state-row">
            <span className="state-name">核心引擎</span>
            <span
              className="state-dot"
              data-status={status ?? "down"}
              aria-label={statusLabel(status)}
            />
          </div>
          <div className="state-meta">
            {health
              ? `Worker v${health.version} · ${Math.floor(health.uptime_seconds)}s`
              : "Worker 初始化中…"}
          </div>
        </div>
      </div>
    </aside>
  );
}
