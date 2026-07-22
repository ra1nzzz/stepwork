import { useHealthStore } from "@/stores/useHealthStore";
import { useViewStore, type ViewId } from "@/stores/useViewStore";

interface NavItemDef {
  id: ViewId;
  label: string;
  icon: string;
}

const NAV_ITEMS: NavItemDef[] = [
  { id: "home", label: "概览", icon: "01" },
  { id: "import", label: "素材导入", icon: "02" },
  { id: "transcript", label: "转写", icon: "03" },
  { id: "analysis", label: "内容分析", icon: "04" },
  { id: "script", label: "脚本创作", icon: "05" },
  { id: "render", label: "视频渲染", icon: "06" },
];

function statusLabel(status: "ok" | "degraded" | "down" | null): string {
  if (status === "ok") return "运行正常";
  if (status === "degraded") return "性能下降";
  if (status === "down") return "已停止";
  return "连接中";
}

/**
 * 左侧导航栏：复用 Prototype .sidebar 结构，导航项驱动视图切换
 * （W3-W4 Batch3 接入素材导入 / 转写 / 内容分析）。
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
