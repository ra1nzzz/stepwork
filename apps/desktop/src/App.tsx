import { useEffect } from "react";
import { AppShell } from "@/components/AppShell";
import { HealthCard } from "@/components/HealthCard";
import { ErrorGuideCard } from "@/components/ErrorGuideCard";
import { useHealthStore } from "@/stores/useHealthStore";

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

  return (
    <AppShell>
      <HealthCard />
    </AppShell>
  );
}
