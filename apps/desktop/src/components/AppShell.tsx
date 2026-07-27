import type { ReactNode } from "react";
import { useEffect } from "react";
import { Sidebar } from "./Sidebar";
import { Footer } from "./Footer";
import { DebugConsole } from "./DebugConsole";
import { useDebugStore } from "@/stores/useDebugStore";

interface AppShellProps {
  /** 顶部 topbar 面包屑区域 */
  crumbs?: ReactNode;
  /** 顶部 topbar 右侧操作区 */
  topActions?: ReactNode;
  children: ReactNode;
}

/**
 * 应用骨架：与 Prototype .app-shell 结构 1:1 对齐
 *   .app-shell
 *     .sidebar  (固定 232px)
 *     .workspace
 *       .topbar (sticky 72px, crumbs + top-actions)
 *       .content (max-width 1480px, padding 30/32px)
 *       .app-footer (运维 Footer)
 *   .debug-console-wrapper (W9 调试控制台)
 */
export function AppShell({ crumbs, topActions, children }: AppShellProps) {
  const initDebug = useDebugStore((s) => s.init);

  useEffect(() => {
    void initDebug();
  }, [initDebug]);

  return (
    <div className="app-shell">
      <Sidebar />
      <div className="workspace">
        {(crumbs || topActions) && (
          <header className="topbar">
            <div className="crumbs">{crumbs}</div>
            {topActions && <div className="top-actions">{topActions}</div>}
          </header>
        )}
        <main className="content">{children}</main>
        <Footer />
      </div>
      <DebugConsole />
    </div>
  );
}
