/**
 * 渲染模板的**前端兜底清单**（PRD-REN-005）。
 *
 * 权威来源是后端注册表（`ListRenderTemplates` → worker/runtime/render/
 * templates.py）。创作页会实时拉取；此清单仅供拉取失败或轻量视图使用，
 * 必须与后端 TEMPLATES 保持同名，否则用户会选到后端不认的模板。
 */
export const RENDER_TEMPLATE_FALLBACK = [
  "vertical-caption-v1",
  "vertical-story-v1",
  "landscape-caption-v1",
  "square-caption-v1",
] as const;
