/**
 * 设置 store 的**密钥不落盘**保证（store 头部注释里的硬承诺）。
 *
 * 这类承诺极易变成装饰性的：`partialize` 写了、注释也写了，但只要
 * 有人改一处合并逻辑就悄悄失效，而 UI 上看不出任何异常。所以这里不测
 * 「函数被调用了」，而是直接翻 **localStorage 里真正写下去的字节**，
 * 断言明文密钥一个字符都不在里面。
 */
import { describe, it, expect, beforeEach } from "vitest";
import { useSettingsStore } from "./useSettingsStore";

const STORAGE_KEY = "stepwork-settings";
const SECRET = "sk-live-DO-NOT-PERSIST-4f2b91";

/** 返回 zustand persist 实际写入 localStorage 的原始字符串。 */
function persistedRaw(): string {
  return globalThis.localStorage.getItem(STORAGE_KEY) ?? "";
}

beforeEach(() => {
  globalThis.localStorage.clear();
  useSettingsStore.getState().reset();
});

describe("API Key 绝不写入 localStorage", () => {
  it("三个 provider 的 apiKey 都不出现在持久化字节里", () => {
    useSettingsStore.getState().update({
      llm: { apiKey: `${SECRET}-llm` } as never,
      asr: { apiKey: `${SECRET}-asr` } as never,
      tts: { apiKey: `${SECRET}-tts` } as never,
    });

    const raw = persistedRaw();
    expect(raw).not.toBe(""); // 确认确实写盘了，否则本测试是空转
    expect(raw).not.toContain(SECRET);
    expect(raw.toLowerCase()).not.toContain("sk-live");
  });

  it("内存中仍可读到 key（供保存时上行），只是不落盘", () => {
    useSettingsStore.getState().update({ llm: { apiKey: SECRET } as never });
    expect(useSettingsStore.getState().settings.llm.apiKey).toBe(SECRET);
    expect(persistedRaw()).not.toContain(SECRET);
  });

  it("非约定命名的密钥字段（token/secret/password）同样被剔除", () => {
    useSettingsStore.getState().update({
      llm: {
        token: `${SECRET}-token`,
        clientSecret: `${SECRET}-secret`,
        password: `${SECRET}-pwd`,
        refreshCredential: `${SECRET}-cred`,
      } as never,
    });
    expect(persistedRaw()).not.toContain(SECRET);
  });

  it("非密钥字段照常持久化（剔除不能误伤正常配置）", () => {
    useSettingsStore.getState().update({
      llm: { model: "gpt-4o-mini", baseUrl: "https://example.test/v1" } as never,
    });
    const raw = persistedRaw();
    expect(raw).toContain("gpt-4o-mini");
    expect(raw).toContain("https://example.test/v1");
    // passkey 结尾不该被 SECRET_RE 误判为密钥
    useSettingsStore.getState().update({ llm: { passkey: "visible" } as never });
    expect(persistedRaw()).toContain("visible");
  });

  it("重新水合后 apiKey 回落空串，而不是 undefined", () => {
    useSettingsStore.getState().update({ llm: { apiKey: SECRET } as never });
    // 模拟重启：用已落盘的内容重新水合
    useSettingsStore.persist.rehydrate();
    const llm = useSettingsStore.getState().settings.llm;
    expect(llm.apiKey).toBe("");
    expect(llm.apiKey).not.toBeUndefined();
  });
});

describe("深合并语义", () => {
  it("按 section 合并，不覆盖同 section 内未提供的字段", () => {
    const before = useSettingsStore.getState().settings.llm.model;
    useSettingsStore.getState().update({ llm: { baseUrl: "https://x.test" } as never });
    expect(useSettingsStore.getState().settings.llm.model).toBe(before);
    expect(useSettingsStore.getState().settings.llm.baseUrl).toBe("https://x.test");
  });

  it("数组整体替换而非拼接（用户勾选的是完整集合）", () => {
    useSettingsStore.getState().update({ brand: { mustExecute: ["a", "b"] } as never });
    useSettingsStore.getState().update({ brand: { mustExecute: ["c"] } as never });
    expect(useSettingsStore.getState().settings.brand.mustExecute).toEqual(["c"]);
  });

  it("清空数组能真正清空（勾掉最后一项不能被当成「没提供」）", () => {
    useSettingsStore.getState().update({ brand: { mustExecute: ["check-similarity"] } as never });
    useSettingsStore.getState().update({ brand: { mustExecute: [] } as never });
    expect(useSettingsStore.getState().settings.brand.mustExecute).toEqual([]);
  });

  it("嵌套对象（llm.sampling）逐字段合并", () => {
    const topP = useSettingsStore.getState().settings.llm.sampling.topP;
    useSettingsStore.getState().update({ llm: { sampling: { temperature: 0.9 } } as never });
    const s = useSettingsStore.getState().settings.llm.sampling;
    expect(s.temperature).toBe(0.9);
    expect(s.topP).toBe(topP);
  });

  it("update 后 savedAt 复位（避免「已保存」标记停留在过期状态）", () => {
    useSettingsStore.getState().markSaved();
    expect(useSettingsStore.getState().savedAt).not.toBeNull();
    useSettingsStore.getState().update({ ui: { theme: "light" } as never });
    expect(useSettingsStore.getState().savedAt).toBeNull();
  });
});
