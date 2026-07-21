import { useHealthStore } from "@/stores/useHealthStore";

interface NavItemDef {
  id: string;
  label: string;
  icon: string;
  active: boolean;
}

const NAV_ITEMS: NavItemDef[] = [
  { id: "home", label: "首页", icon: "01", active: true },
  { id: "projects", label: "项目", icon: "02", active: false },
  { id: "create", label: "创作", icon: "03", active: false },
  { id: "tasks", label: "任务", icon: "04", active: false },
  { id: "settings", label: "设置", icon: "05", active: false },
];

function statusLabel(status: "ok" | "degraded" | "down" | null): string {
  if (status === "ok") return "运行正常";
  if (status === "degraded") return "性能下降";
  if (status === "down") return "已停止";
  return "连接中";
}

/**
 * 左侧导航栏：复用 Prototype .sidebar 结构（index.html L57-159）
 */
export function Sidebar() {
  const health = useHealthStore((s) => s.health);
  const status = health?.status ?? null;

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
            className={`nav-item${item.active ? " active" : ""}`}
            disabled={!item.active}
            aria-current={item.active ? "page" : undefined}
            data-od-id={`nav-${item.id}`}
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
