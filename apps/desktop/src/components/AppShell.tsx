import type { ReactNode } from "react";
import { Sidebar } from "./Sidebar";

interface AppShellProps {
  children: ReactNode;
}

/**
 * 应用骨架：左侧固定 Sidebar（var(--sidebar) 宽），右侧主区
 * 与 Prototype .app-shell 结构 1:1 对齐
 */
export function AppShell({ children }: AppShellProps) {
  return (
    <div className="app-shell">
      <Sidebar />
      <main className="app-main">
        <div className="app-main-inner">{children}</div>
      </main>
    </div>
  );
}
