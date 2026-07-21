import { useEffect } from "react";
import { useAppInfoStore } from "@/stores/useAppInfoStore";

/**
 * 运维 Footer（W2 carry-over #5）
 * 展示应用元信息 + sidecar 自愈指标（restart_count / last_crash_at）。
 *
 * - restart_count > 0 时高亮，提示发生过自愈重启；
 * - last_crash_at 为 null 时显示「无」，否则显示 ISO 时间（本地化）。
 */
export function Footer() {
  const appInfo = useAppInfoStore((s) => s.appInfo);
  const isLoading = useAppInfoStore((s) => s.isLoading);
  const fetchAppInfo = useAppInfoStore((s) => s.fetchAppInfo);

  useEffect(() => {
    void fetchAppInfo();
  }, [fetchAppInfo]);

  if (isLoading || !appInfo) {
    return (
      <footer className="app-footer" aria-busy="true">
        <span className="app-footer-item app-footer-muted">加载应用信息…</span>
      </footer>
    );
  }

  const restarted = appInfo.restart_count > 0;
  const crashLabel = appInfo.last_crash_at
    ? new Date(appInfo.last_crash_at).toLocaleString()
    : "无";

  return (
    <footer className="app-footer">
      <span className="app-footer-item">
        <span className="app-footer-key">版本</span>
        {appInfo.version}
      </span>
      <span className="app-footer-item">
        <span className="app-footer-key">平台</span>
        {appInfo.platform}
      </span>
      <span className="app-footer-item app-footer-path" title={appInfo.stepwork_home}>
        <span className="app-footer-key">Home</span>
        {appInfo.stepwork_home}
      </span>
      <span className={`app-footer-item${restarted ? " app-footer-warn" : ""}`}>
        <span className="app-footer-key">自愈重启</span>
        {appInfo.restart_count} 次
      </span>
      <span className="app-footer-item">
        <span className="app-footer-key">最近崩溃</span>
        {crashLabel}
      </span>
    </footer>
  );
}
