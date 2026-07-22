/**
 * 版本链（W5 Batch1）
 * 展示 SaveScript 自动保存形成的 parent 链（最新在最上）。
 */

import { useScriptStore } from "@/stores/useScriptStore";

export function VersionHistory() {
  const chain = useScriptStore((s) => s.versionChain);

  return (
    <aside className="version-history" data-od-id="version-history">
      <h3>版本链</h3>
      {chain.length === 0 ? (
        <p className="muted">暂无保存版本</p>
      ) : (
        <ol className="version-list">
          {chain.map((v, i) => (
            <li key={v.id} data-od-id={`version-${i}`}>
              <span className="version-kind">{v.producer_kind ?? "—"}</span>
              <span className="version-time">
                {new Date(v.created_at).toLocaleTimeString()}
              </span>
            </li>
          ))}
        </ol>
      )}
    </aside>
  );
}
