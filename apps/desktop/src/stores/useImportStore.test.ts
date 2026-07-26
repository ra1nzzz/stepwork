/**
 * 导入 store 的去重提示（PRD-SRC-004 的验收是「重复导入时**提示用户**」）。
 *
 * 后端算哈希去重此前有测试，但「提示用户」这半边全在前端，一直没有测过——
 * 而它恰恰是验收标准里写明的那半边：后端默默复用了既有素材，用户如果看不到
 * 提示，会以为自己刚才那次导入没生效，然后反复重试。
 *
 * 这里把 dispatchCommand 换成桩，直接验证 store 的状态迁移。
 */
import { describe, it, expect, beforeEach, vi } from "vitest";

const dispatchCommand = vi.fn();
vi.mock("@/lib/tauri", () => ({
  dispatchCommand: (...args: unknown[]) => dispatchCommand(...args),
  buildEnvelope: (commandType: string, _ws: string, _p: unknown, payload: unknown) => ({
    commandType,
    payload,
  }),
  getWorkspaceId: () => "ws-test",
}));

const { useImportStore } = await import("./useImportStore");
const { useViewStore } = await import("./useViewStore");

/** 构造一条后端返回：dedup 命中与否可控 */
function importResult(assetId: string, dedup: boolean) {
  return {
    ok: true,
    commandId: "c",
    job_id: null,
    artifact_ids: [assetId],
    error: null,
    detail: { dedup },
  };
}

/** importFiles 的入参形状（ImportFileInput）：uri/name/mimeType 必备 */
const FILE = {
  name: "素材.mp4",
  uri: "D:/tmp/素材.mp4",
  mimeType: "video/mp4",
  sizeBytes: 1024,
};

beforeEach(() => {
  dispatchCommand.mockReset();
  useImportStore.setState({ assets: [], dedupNotice: null, error: null, isBusy: false });
  // 预设当前项目，避免 importFiles 先去建项目（那会多吃一次 mock 返回）
  useViewStore.getState().setSelectedProjectId("proj-1", "测试项目");
});

describe("PRD-SRC-004 重复导入时提示用户", () => {
  it("dedup 命中时给出可读提示且带上文件名", async () => {
    dispatchCommand.mockResolvedValue(importResult("asset-1", true));
    await useImportStore.getState().importFiles([FILE]);

    const notice = useImportStore.getState().dedupNotice;
    expect(notice).toBeTruthy();
    // 提示必须点名是哪个文件，否则批量导入时用户不知道说的是谁
    expect(notice).toContain("素材.mp4");
    expect(notice).toContain("已导入过");
  });

  it("未命中 dedup 时不显示提示（否则提示变噪声，用户就不看了）", async () => {
    dispatchCommand.mockResolvedValue(importResult("asset-1", false));
    await useImportStore.getState().importFiles([FILE]);
    expect(useImportStore.getState().dedupNotice).toBeNull();
  });

  it("新一轮导入会清掉上一轮的提示，不残留", async () => {
    dispatchCommand.mockResolvedValue(importResult("asset-1", true));
    await useImportStore.getState().importFiles([FILE]);
    expect(useImportStore.getState().dedupNotice).toBeTruthy();

    dispatchCommand.mockResolvedValue(importResult("asset-2", false));
    await useImportStore.getState().importFiles([
      { ...FILE, name: "另一个.mp4", uri: "D:/tmp/b.mp4" },
    ]);
    expect(useImportStore.getState().dedupNotice).toBeNull();
  });

  it("dedup 命中不重复往列表里塞同一条素材", async () => {
    dispatchCommand.mockResolvedValue(importResult("asset-1", false));
    await useImportStore.getState().importFiles([FILE]);
    expect(useImportStore.getState().assets).toHaveLength(1);

    // 同一个 asset id 再回来一次（后端复用了既有记录）
    dispatchCommand.mockResolvedValue(importResult("asset-1", true));
    await useImportStore.getState().importFiles([FILE]);
    expect(useImportStore.getState().assets).toHaveLength(1);
    expect(useImportStore.getState().dedupNotice).toBeTruthy();
  });

  it("后端没返回 asset_id 时报错而不是静默塞条坏记录", async () => {
    dispatchCommand.mockResolvedValue({
      ok: true,
      commandId: "c",
      job_id: null,
      artifact_ids: [],
      error: null,
      detail: {},
    });
    await useImportStore.getState().importFiles([FILE]);
    expect(useImportStore.getState().error).toBeTruthy();
    expect(useImportStore.getState().assets).toHaveLength(0);
  });
});
