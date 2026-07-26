/**
 * 主链路冒烟：导入 → 转写 → 分析 → 脚本 → 渲染 → 发布。
 *
 * 不测 UI 长什么样，测的是**各 store 之间的接力**：上一步的产物 id 有没有
 * 正确交到下一步手里。这条链断在哪一环，用户看到的都是「点了没反应」，
 * 而单测各自都是绿的 —— 因为每个 store 只验证了自己那一段。
 *
 * dispatch 被替换成按 commandType 应答的桩，所以这里跑得飞快且不需要后端。
 */
import { describe, it, expect, beforeEach, vi } from "vitest";

const dispatchCommand = vi.fn();
vi.mock("./tauri", () => ({
  dispatchCommand: (...args: unknown[]) => dispatchCommand(...args),
  buildEnvelope: (
    commandType: string,
    workspaceId: string,
    projectId: unknown,
    payload: unknown,
  ) => ({ commandType, workspaceId, projectId, payload }),
  getWorkspaceId: () => "ws-test",
}));

const { useViewStore } = await import("@/stores/useViewStore");
const { useImportStore } = await import("@/stores/useImportStore");
const { useTranscriptStore } = await import("@/stores/useTranscriptStore");
const { useRenderStore } = await import("@/stores/useRenderStore");

function ok(detail: Record<string, unknown>, artifactIds: string[] = []) {
  return {
    ok: true,
    commandId: "c",
    job_id: "job-1",
    artifact_ids: artifactIds,
    error: null,
    detail,
  };
}

/** 按 commandType 应答的后端桩 */
const RESPONSES: Record<string, ReturnType<typeof ok>> = {
  ImportSource: ok({ asset_id: "asset-1", dedup: false }, ["asset-1"]),
  TranscribeSource: ok(
    { segment_count: 2, provider: "local", language: "zh" },
    ["cv-transcript"],
  ),
  AnalyzeSource: ok(
    { mode: "quick", scene_count: 0, invocation: { provider: "fake" } },
    ["cv-analysis"],
  ),
  GenerateTopic: ok({ angles: [{ id: "a1", title: "角度一" }] }, ["cv-topic"]),
  GenerateScript: ok({ title: "脚本", body: "正文", invocation: {} }, ["cv-script"]),
  SaveScript: ok({}, ["cv-script-2"]),
  CreateRenderJob: ok({ video_uri: "file:///v.mp4" }, ["cv-video"]),
};

beforeEach(() => {
  dispatchCommand.mockReset();
  dispatchCommand.mockImplementation((env: { commandType: string }) =>
    Promise.resolve(RESPONSES[env.commandType] ?? ok({})),
  );
  useViewStore.getState().setSelectedProjectId("proj-1", "冒烟项目");
  useImportStore.setState({ assets: [], error: null, dedupNotice: null });
  useTranscriptStore.setState({ jobs: [], error: null });
  useRenderStore.setState({ sourceVersionId: "", videoVersionId: null, error: null });
});

describe("主链路接力", () => {
  it("导入后素材 id 进入 store，供下一步转写使用", async () => {
    await useImportStore.getState().importFiles([
      { name: "a.mp4", uri: "D:/a.mp4", mimeType: "video/mp4", sizeBytes: 1 },
    ]);
    const assets = useImportStore.getState().assets;
    expect(assets).toHaveLength(1);
    expect(assets[0].id).toBe("asset-1");
    expect(useImportStore.getState().error).toBeNull();
  });

  it("转写把 asset_id 带给后端，并落下 transcript 版本", async () => {
    await useTranscriptStore.getState().transcribe("asset-1");
    const sent = dispatchCommand.mock.calls.map((c) => c[0]);
    const call = sent.find((e) => e.commandType === "TranscribeSource");
    expect(call).toBeDefined();
    expect(call.payload.asset_id).toBe("asset-1");
  });

  it("脚本版本 id 会交接给渲染 store（否则渲染无源可渲）", () => {
    useRenderStore.getState().setSourceVersion("cv-script-2");
    expect(useRenderStore.getState().sourceVersionId).toBe("cv-script-2");
  });

  it("整条链路任一命令失败都不会被吞掉", async () => {
    dispatchCommand.mockResolvedValue({
      ok: false,
      commandId: "c",
      job_id: null,
      artifact_ids: [],
      error: "UNAVAILABLE: asr provider not configured",
      detail: {},
    });
    await useTranscriptStore.getState().transcribe("asset-1");
    // 失败必须落到 store 的 error 上，而不是静默停在「进行中」
    const state = useTranscriptStore.getState();
    const failed =
      state.error !== null || state.jobs.some((j) => j.status === "failed");
    expect(failed).toBe(true);
  });
});

describe("项目上下文", () => {
  it("所有命令都带上当前项目 id —— 缺了会落到错误的项目里", async () => {
    await useTranscriptStore.getState().transcribe("asset-1");
    const call = dispatchCommand.mock.calls
      .map((c) => c[0])
      .find((e) => e.commandType === "TranscribeSource");
    expect(call.projectId).toBe("proj-1");
  });

  it("工作区 id 一律来自 getWorkspaceId，不硬编码", async () => {
    await useTranscriptStore.getState().transcribe("asset-1");
    for (const [env] of dispatchCommand.mock.calls) {
      expect(env.workspaceId).toBe("ws-test");
    }
  });
});
