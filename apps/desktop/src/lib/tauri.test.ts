/**
 * `lib/tauri.ts` 的跨边界契约测试。
 *
 * 这里测的不是 UI 长什么样，而是**前端发出去的东西后端认不认**：
 * `buildEnvelope` 的产物必须逐字段满足 `schemas/command-envelope.schema.json`
 * （该 schema 是 UI / Rust / Python 三端的单一事实源）。此前这条边界只有
 * Python 侧在校验 —— 前端拼错字段要等到运行时被 worker 拒绝才发现。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import {
  buildEnvelope,
  getWorkspaceId,
  setWorkspaceId,
  dispatchCommand,
  DEFAULT_WORKSPACE_ID,
  resetBridgeDetection,
} from "./tauri";

/**
 * 直接读仓库里的 schema，而不是在测试里抄一份（抄了就会漂移）。
 * vitest 的 root 是 `apps/desktop`，故相对仓库根回退两级。
 */
const SCHEMA_PATH = resolve(
  process.cwd(),
  "../../schemas/command-envelope.schema.json",
);
const schema = JSON.parse(readFileSync(SCHEMA_PATH, "utf-8")) as {
  required: string[];
  additionalProperties: boolean;
  properties: Record<string, { enum?: string[]; required?: string[] }>;
};

describe("buildEnvelope 满足 command-envelope.schema.json", () => {
  const env = buildEnvelope("ListProjects", "ws-a", "proj-1", { a: 1 });

  it("包含 schema 的全部必填字段", () => {
    for (const key of schema.required) {
      expect(env, `缺少必填字段 ${key}`).toHaveProperty(key);
      expect((env as unknown as Record<string, unknown>)[key]).not.toBeUndefined();
    }
  });

  it("不含 schema 之外的字段（additionalProperties: false）", () => {
    const allowed = new Set(Object.keys(schema.properties));
    const extra = Object.keys(env).filter((k) => !allowed.has(k));
    expect(extra, `多出 schema 未声明的字段：${extra.join(", ")}`).toEqual([]);
  });

  it("actor / source 取值在 schema 枚举内", () => {
    expect(schema.properties.actor.required).toEqual(["type", "id"]);
    expect(env.actor.id.length).toBeGreaterThan(0);
    expect(schema.properties.source.enum).toContain(env.source);
  });

  it("commandType 是 schema 枚举里的合法命令", () => {
    expect(schema.properties.commandType.enum).toContain(env.commandType);
  });

  it("requestedAt 是可解析的 ISO 时间戳", () => {
    expect(Number.isNaN(Date.parse(env.requestedAt))).toBe(false);
  });

  it("commandId 每次唯一（后端按它做去重/审计）", () => {
    const ids = new Set(
      Array.from({ length: 50 }, () => buildEnvelope("ListProjects", "ws", null, {}).commandId),
    );
    expect(ids.size).toBe(50);
  });

  it("幂等键缺省为 null 而非 undefined（undefined 会被 JSON 丢字段）", () => {
    expect(env.idempotencyKey).toBeNull();
    expect(JSON.parse(JSON.stringify(env))).toHaveProperty("idempotencyKey");
    expect(buildEnvelope("ListProjects", "ws", null, {}, "k-1").idempotencyKey).toBe("k-1");
  });
});

describe("工作区上下文持久化", () => {
  it("未选择时回落默认工作区", () => {
    expect(getWorkspaceId()).toBe(DEFAULT_WORKSPACE_ID);
  });

  it("切换后跨读取保持（模拟重启）", () => {
    setWorkspaceId("ws-team");
    expect(getWorkspaceId()).toBe("ws-team");
  });

  it("localStorage 不可用时不抛错，降级为默认值", () => {
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    expect(() => getWorkspaceId()).not.toThrow();
    expect(getWorkspaceId()).toBe(DEFAULT_WORKSPACE_ID);
    spy.mockRestore();

    const setSpy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    expect(() => setWorkspaceId("ws-x")).not.toThrow();
    setSpy.mockRestore();
  });
});

describe("未连接后端时的行为", () => {
  beforeEach(() => {
    resetBridgeDetection();
    // 探测 dev_bridge 会真发 fetch；这里模拟「桥也没起」
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("ECONNREFUSED"))),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    resetBridgeDetection();
  });

  it("返回明确错误，绝不返回伪造的成功数据", async () => {
    const env = buildEnvelope("ListProjects", "ws-a", null, {});
    const res = await dispatchCommand(env);
    expect(res.ok).toBe(false);
    expect(res.error).toBe("BACKEND_NOT_CONNECTED");
    // 回声 commandId，便于 UI 把错误关联回具体请求
    expect(res.commandId).toBe(env.commandId);
    expect(res.artifact_ids).toEqual([]);
    expect(res.job_id).toBeNull();
  });
});
