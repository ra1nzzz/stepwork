import { useEffect, type ReactNode } from "react";
import { AppShell } from "@/components/AppShell";
import { ErrorGuideCard } from "@/components/ErrorGuideCard";
import { useHealthStore } from "@/stores/useHealthStore";
import { useViewStore } from "@/stores/useViewStore";
import { useTranscriptStore } from "@/stores/useTranscriptStore";
import { useRenderStore } from "@/stores/useRenderStore";
import { WorkbenchView } from "@/features/home/WorkbenchView";
import { ProjectsView } from "@/features/projects/ProjectsView";
import { CreateView } from "@/features/create/CreateView";
import { TasksView } from "@/features/tasks/TasksView";
import SettingsView from "@/features/settings/SettingsView";
import { DiagnosticsView } from "@/features/diagnostics/DiagnosticsView";

function Splash() {
  return (
    <div className="splash" role="status" aria-live="polite">
      <div className="splash-inner">
        <div className="splash-logo" aria-hidden="true" />
        <div className="splash-text">引擎初始化中…</div>
      </div>
    </div>
  );
}

/** 与原型 .topbar 一致：每个视图自带 crumbs + top-actions */
function buildTopBar(
  view: string,
  failedCount: number,
): { crumbs: ReactNode; topActions: ReactNode } {
  switch (view) {
    case "home":
      return {
        crumbs: (
          <>
            <span>STEPWORK</span>
            <span>/</span>
            <strong>首页</strong>
          </>
        ),
        topActions: (
          <>
            <button
              className="btn ghost"
              type="button"
              onClick={() => useViewStore.getState().setView("tasks")}
              data-od-id="open-task-center"
            >
              {failedCount > 0 ? `任务中心 · ${failedCount} 个异常` : "任务中心"}
            </button>
            <button
              className="btn primary"
              type="button"
              onClick={() => useViewStore.getState().setView("projects")}
              data-od-id="new-project-button"
            >
              新建项目
            </button>
          </>
        ),
      };
    case "projects":
      return {
        crumbs: <strong>项目</strong>,
        topActions: null,
      };
    case "create":
      return {
        crumbs: (
          <>
            <span>AI 眼镜通勤实测</span>
            <span>/</span>
            <strong>创作</strong>
          </>
        ),
        topActions: null,
      };
    case "tasks":
      return {
        crumbs: <strong>任务</strong>,
        topActions: null,
      };
    case "settings":
      return {
        crumbs: <strong>设置</strong>,
        topActions: null,
      };
    case "diagnostics":
      return {
        crumbs: (
          <>
            <strong>设置</strong>
            <span>/</span>
            <span>诊断</span>
          </>
        ),
        topActions: null,
      };
    default:
      return { crumbs: null, topActions: null };
  }
}

export default function App() {
  const health = useHealthStore((s) => s.health);
  const error = useHealthStore((s) => s.error);
  const isLoading = useHealthStore((s) => s.isLoading);
  const startPolling = useHealthStore((s) => s.startPolling);
  const stopPolling = useHealthStore((s) => s.stopPolling);
  const currentView = useViewStore((s) => s.currentView);

  // 任务中心异常数：从 transcript/render store 派生真实失败计数
  const transcriptJobs = useTranscriptStore((s) => s.jobs);
  const renderStatus = useRenderStore((s) => s.status);
  const failedCount =
    transcriptJobs.filter((j) => j.status === "failed").length +
    (renderStatus === "failed" ? 1 : 0);

  useEffect(() => {
    startPolling();
    return () => {
      stopPolling();
    };
  }, [startPolling, stopPolling]);

  // 首屏 splash：尚未拿到任何数据且仍在加载
  if (!health && isLoading && !error) {
    return <Splash />;
  }

  // 启动失败且从未成功：错误引导卡全屏
  if (error && !health) {
    return (
      <div className="splash">
        <ErrorGuideCard error={error} />
      </div>
    );
  }

  let content: ReactNode;
  switch (currentView) {
    case "projects":
      content = <ProjectsView />;
      break;
    case "create":
      content = <CreateView />;
      break;
    case "tasks":
      content = <TasksView />;
      break;
    case "settings":
      content = <SettingsView />;
      break;
    case "diagnostics":
      content = <DiagnosticsView />;
      break;
    case "home":
    default:
      content = <WorkbenchView />;
      break;
  }

  const { crumbs, topActions } = buildTopBar(currentView, failedCount);

  return (
    <AppShell crumbs={crumbs} topActions={topActions}>
      {content}
    </AppShell>
  );
}
