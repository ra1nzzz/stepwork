import { useEffect, useRef } from "react";
import { useDebugStore } from "@/stores/useDebugStore";

/**
 * 调试控制台抽屉（W9 增补）
 *
 * - 从 Tauri event "worker://log" 订阅 worker stderr 日志
 * - 底部抽屉，可展开/收起
 * - 配置 console=false 时不渲染
 * - 自动滚动到最新行
 */
export function DebugConsole() {
  const config = useDebugStore((s) => s.config);
  const logs = useDebugStore((s) => s.logs);
  const isPanelOpen = useDebugStore((s) => s.isPanelOpen);
  const togglePanel = useDebugStore((s) => s.togglePanel);
  const clearLogs = useDebugStore((s) => s.clearLogs);
  const logEndRef = useRef<HTMLDivElement | null>(null);

  // 自动滚动到底部
  useEffect(() => {
    if (isPanelOpen && logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [logs, isPanelOpen]);

  // console 关闭时不渲染控制台
  if (!config.console) return null;

  return (
    <div className="debug-console-wrapper" data-od-id="debug-console">
      {/* 收起态：底部小条 */}
      {!isPanelOpen && (
        <button
          type="button"
          className="debug-console-bar"
          onClick={togglePanel}
          data-od-id="debug-console-toggle"
        >
          <span className="debug-console-badge" aria-hidden="true">
            {logs.length > 0 ? logs.length : ""}
          </span>
          调试控制台
        </button>
      )}

      {/* 展开态：抽屉 */}
      {isPanelOpen && (
        <section className="debug-console-panel" aria-label="调试控制台">
          <header className="debug-console-head">
            <span className="debug-console-title">
              调试控制台
              <span className="debug-console-count">({logs.length})</span>
            </span>
            <div className="debug-console-actions">
              <button
                type="button"
                className="btn ghost small"
                onClick={clearLogs}
              >
                清空
              </button>
              <button
                type="button"
                className="btn ghost small"
                onClick={togglePanel}
              >
                收起
              </button>
            </div>
          </header>
          <div className="debug-console-body">
            {logs.length === 0 ? (
              <p className="debug-console-empty">暂无日志（等待 worker 输出…）</p>
            ) : (
              <ul className="debug-log-list">
                {logs.map((entry, idx) => (
                  <li key={idx} className="debug-log-line" data-od-id={`log-${idx}`}>
                    <span className="debug-log-ts">
                      {entry.ts
                        ? new Date(entry.ts).toLocaleTimeString("zh-CN", {
                            hour12: false,
                          })
                        : ""}
                    </span>
                    <span className="debug-log-text">{entry.line}</span>
                  </li>
                ))}
              </ul>
            )}
            <div ref={logEndRef} />
          </div>
        </section>
      )}
    </div>
  );
}
