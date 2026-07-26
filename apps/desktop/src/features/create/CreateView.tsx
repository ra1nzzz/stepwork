/**
 * 创作页容器 — 与 Prototype .subnav 4 步流程对齐
 *
 * 子视图（与 useViewStore.CreateSubView 对应）：
 *   import   → 01 素材分析（顶部：来源导入 + 分析方式）
 *   analysis → 01 素材分析（底部：分析任务 + 逐字稿 + 结构化报告）
 *   angle    → 02 原创角度
 *   script   → 03 脚本创作
 *   render   → 04 视频草稿
 *
 * 由于原型 inspector.html 把"导入"和"分析"放在同一页（.subnav 中只显示"01 素材分析"），
 * 我们沿用 5 个内部 subView ID，但 subnav 高亮规则如下：
 *   - import / analysis 都高亮 "01 素材分析"
 *   - 其余一一对应
 */

import { useViewStore, type CreateSubView } from "@/stores/useViewStore";
import { CreateImportView } from "./CreateImportView";
import { CreateAnalysisView } from "./CreateAnalysisView";
import { CreateAngleView } from "./CreateAngleView";
import { CreateScriptView } from "./CreateScriptView";
import { CreateRenderView } from "./CreateRenderView";

interface SubnavItem {
  sub: CreateSubView;
  label: string;
}

const SUBNAV: SubnavItem[] = [
  { sub: "import", label: "01 素材分析" },
  { sub: "angle", label: "02 原创角度" },
  { sub: "script", label: "03 脚本创作" },
  { sub: "render", label: "04 视频草稿" },
];

export function CreateView() {
  const createSubView = useViewStore((s) => s.createSubView);
  const setCreateSubView = useViewStore((s) => s.setCreateSubView);

  // import / analysis 共用 "01 素材分析" 高亮
  const activeLabel = (() => {
    if (createSubView === "import" || createSubView === "analysis") {
      return "01 素材分析";
    }
    return SUBNAV.find((x) => x.sub === createSubView)?.label ?? "";
  })();

  return (
    <>
      <nav className="subnav" aria-label="创作流程">
        {SUBNAV.map((item) => (
          <button
            key={item.sub}
            type="button"
            className={activeLabel === item.label ? "active" : ""}
            onClick={() => setCreateSubView(item.sub)}
          >
            {item.label}
          </button>
        ))}
      </nav>

      {createSubView === "import" && <CreateImportView />}
      {createSubView === "analysis" && <CreateAnalysisView />}
      {createSubView === "angle" && <CreateAngleView />}
      {createSubView === "script" && <CreateScriptView />}
      {createSubView === "render" && <CreateRenderView />}
    </>
  );
}
