/**
 * 视图路由 Store
 * 对齐 Prototype 5 项一级导航：首页 / 项目 / 创作 / 任务 / 设置
 * "创作"页内部通过 subnav 切换子视图（import/analysis/angle/script/render）
 *
 * selectedProjectId：当前选中的项目 id，贯穿整个创作流程，
 * 由 ProjectsView「继续」/「新建项目」或 WorkbenchView 项目行点击写入；
 * useImportStore / useTranscriptStore 等会读取它作为 buildEnvelope.projectId。
 */

import { create } from "zustand";

export type ViewId =
  | "home"
  | "projects"
  | "create"
  | "tasks"
  | "settings"
  | "diagnostics";

/** 创作页内部的子视图（对应原型 .subnav） */
export type CreateSubView =
  | "import"
  | "analysis"
  | "angle"
  | "script"
  | "render";

interface ViewStoreState {
  currentView: ViewId;
  /** 创作页当前子视图 */
  createSubView: CreateSubView;
  /** 当前选中的项目 id（贯穿创作流程，下游 store 读取） */
  selectedProjectId: string | null;
  setView: (v: ViewId) => void;
  setCreateSubView: (v: CreateSubView) => void;
  setSelectedProjectId: (id: string | null) => void;
}

export const useViewStore = create<ViewStoreState>((set) => ({
  currentView: "home",
  createSubView: "import",
  selectedProjectId: null,
  setView: (v) => set({ currentView: v }),
  setCreateSubView: (v) => set({ createSubView: v }),
  setSelectedProjectId: (id) => set({ selectedProjectId: id }),
}));
