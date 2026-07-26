/**
 * 数据层的核心保证：**失败不可能被静默忽略**。
 *
 * 改造前 79 处手写调用里有 10 处忘了检查 `res.ok`，用户点了按钮没反应也看不到
 * 原因。这里锁住的不是「某个调用点记得检查」，而是「根本没有可以忘的步骤」——
 * `runCommand` 把 ok=false 一律转成异常，调用方要么处理要么让它冒泡。
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
    idempotencyKey: unknown,
  ) => ({ commandType, workspaceId, projectId, payload, idempotencyKey }),
  getWorkspaceId: () => "ws-test",
}));

const { runCommand, CommandError, commandKey, errorText } = await import("./useCommand");

function ok(detail: unknown) {
  return { ok: true, commandId: "c", job_id: null, artifact_ids: [], error: null, detail };
}
function fail(error: string, detail: unknown = null) {
  return { ok: false, commandId: "c", job_id: null, artifact_ids: [], error, detail };
}

beforeEach(() => dispatchCommand.mockReset());

describe("runCommand 不给静默失败留出口", () => {
  it("ok=false 抛 CommandError 而不是返回空 detail", async () => {
    dispatchCommand.mockResolvedValue(fail("CONNECTION_DISABLED: 已停用"));
    await expect(runCommand("DeleteAgentConnection", { connectionId: "c1" })).rejects.toBeInstanceOf(
      CommandError,
    );
  });

  it("错误码原样带出，UI 能显示后端到底为什么拒绝", async () => {
    dispatchCommand.mockResolvedValue(fail("FORBIDDEN_ACTOR: 不在允许清单", { hint: "x" }));
    try {
      await runCommand("DeleteAgentConnection", {});
      expect.unreachable("应当抛错");
    } catch (e) {
      expect(e).toBeInstanceOf(CommandError);
      expect((e as InstanceType<typeof CommandError>).code).toContain("FORBIDDEN_ACTOR");
      expect((e as InstanceType<typeof CommandError>).detail).toEqual({ hint: "x" });
    }
  });

  it("后端没给 error 字段也要有可读兜底，不能抛出空信息", async () => {
    dispatchCommand.mockResolvedValue({ ...fail(""), error: null });
    await expect(runCommand("DeleteAgentConnection", {})).rejects.toThrow("COMMAND_FAILED");
  });

  it("成功时直接返回 detail，调用方不必再解包一层", async () => {
    dispatchCommand.mockResolvedValue(ok({ deleted: "conn_1" }));
    await expect(runCommand("DeleteAgentConnection", {})).resolves.toEqual({
      deleted: "conn_1",
    });
  });

  it("detail 为 null 时回落空对象，避免调用方到处判空", async () => {
    dispatchCommand.mockResolvedValue(ok(null));
    await expect(runCommand("DeleteAgentConnection", {})).resolves.toEqual({});
  });
});

describe("信封构造", () => {
  it("带上工作区、项目与幂等键", async () => {
    dispatchCommand.mockResolvedValue(ok({}));
    await runCommand(
      "SchedulePublish",
      { variantId: "v1" },
      { projectId: "p1", idempotencyKey: "k1" },
    );
    expect(dispatchCommand).toHaveBeenCalledWith({
      commandType: "SchedulePublish",
      workspaceId: "ws-test",
      projectId: "p1",
      payload: { variantId: "v1" },
      idempotencyKey: "k1",
    });
  });

  it("未给项目/幂等键时显式传 null（undefined 会被 JSON 丢字段）", async () => {
    dispatchCommand.mockResolvedValue(ok({}));
    await runCommand("ListAgentConnections");
    const env = dispatchCommand.mock.calls[0][0];
    expect(env.projectId).toBeNull();
    expect(env.idempotencyKey).toBeNull();
  });
});

describe("查询 key", () => {
  it("payload 不同 → key 不同（否则会串用缓存）", () => {
    const a = commandKey("ListPlatformVariants", { projectId: "p1" });
    const b = commandKey("ListPlatformVariants", { projectId: "p2" });
    expect(a).not.toEqual(b);
  });

  it("同输入 → key 相同（否则永远命不中缓存）", () => {
    expect(commandKey("ListAgentConnections", {}, "p1")).toEqual(
      commandKey("ListAgentConnections", {}, "p1"),
    );
  });
});

describe("errorText", () => {
  it("CommandError 取错误码", () => {
    expect(errorText(new CommandError("NOT_FOUND", null))).toBe("NOT_FOUND");
  });
  it("普通 Error 取 message", () => {
    expect(errorText(new Error("boom"))).toBe("boom");
  });
  it("非 Error 也要有可读输出，不能显示 [object Object]", () => {
    expect(errorText(null)).toBe("未知错误");
    expect(errorText("字符串错误")).toBe("字符串错误");
  });
});
