/**
 * 脚本创作视图（W5 Batch1）
 * 左：选题角度；中：TipTap 脚本编辑器（自动保存）；右：版本链
 */

import { TopicView } from "./TopicView";
import { ScriptEditor } from "./ScriptEditor";
import { VersionHistory } from "./VersionHistory";

export function ScriptView() {
  return (
    <div className="script-layout" data-od-id="script-view">
      <TopicView />
      <ScriptEditor />
      <VersionHistory />
    </div>
  );
}
