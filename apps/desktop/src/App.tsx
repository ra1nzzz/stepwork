import { useEffect } from "react";
import { AppShell } from "@/components/AppShell";
import { HealthCard } from "@/components/HealthCard";
import { ErrorGuideCard } from "@/components/ErrorGuideCard";
import { useHealthStore } from "@/stores/useHealthStore";
import { useViewStore } from "@/stores/useViewStore";
import { ImportView } from "@/features/import/ImportView";
import { TranscriptView } from "@/features/transcript/TranscriptView";
import { AnalysisView } from "@/features/analysis/AnalysisView";
import { ScriptView } from "@/features/script/ScriptView";
import { RenderView } from "@/features/render/RenderView";
import SettingsView from "@/features/settings/SettingsView";
import { ProvenanceView } from "@/features/provenance/ProvenanceView";
import { AgentView } from "@/features/agent/AgentView";
import { DiagnosticsView } from "@/features/diagnostics/DiagnosticsView";
import { PluginsView } from "@/features/plugins/PluginsView";

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

export default function App() {
  const health = useHealthStore((s) => s.health);
  const error = useHealthStore((s) => s.error);
  const isLoading = useHealthStore((s) => s.isLoading);
  const startPolling = useHealthStore((s) => s.startPolling);
  const stopPolling = useHealthStore((s) => s.stopPolling);
  const currentView = useViewStore((s) => s.currentView);

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

  let content;
  switch (currentView) {
    case "import":
      content = <ImportView />;
      break;
    case "transcript":
      content = <TranscriptView />;
      break;
    case "analysis":
      content = <AnalysisView />;
      break;
    case "script":
      content = <ScriptView />;
      break;
    case "render":
      content = <RenderView />;
      break;
    case "settings":
      content = <SettingsView />;
      break;
    case "provenance":
      content = <ProvenanceView />;
      break;
    case "agent":
      content = <AgentView />;
      break;
    case "diagnostics":
      content = <DiagnosticsView />;
      break;
    case "plugins":
      content = <PluginsView />;
      break;
    default:
      content = <HealthCard />;
  }

  return <AppShell>{content}</AppShell>;
}
