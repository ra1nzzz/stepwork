/**
 * 发布通道判据层的 L1 测试（S7 前端入口的护栏）。
 *
 * 夹具来源要分清（手搓夹具会把「我以为契约长什么样」当成契约，那正是这类
 * 断裂的成因）：
 *   - `UNAVAILABLE_*` 两条**取自真机 CLI 输出**（2026-09-13，
 *     `stepwork-cli publish provider`，本机未装 opencli）
 *   - `READY` / `NEED_LOGIN` 按 `ProbePublishProviderDetail` 契约构造 ——
 *     本机装不出一个可用的 opencli，取不到真机样本；形状由
 *     `results.generated.ts` 的生成类型锁定
 *
 * 每条断言对应一条**产品红线**，不是覆盖率凑数：
 *   只有 ready 算可用 · 手动发布提示无条件成立 · 两种不可用状态的修法要分开
 */

import { describe, expect, it } from "vitest";
import {
  diagnosticsLine,
  fixHint,
  isUsable,
  MANUAL_PUBLISH_NOTICE,
  stateBadge,
  toProviderStatus,
} from "./providerViewModel";

/** 真机输出：未配置（`STEPWORK_PUBLISH_PROVIDER` 为空）。 */
const UNAVAILABLE_UNSET = {
  state: "unavailable",
  provider: "",
  detail: "没有配置发布 Provider（STEPWORK_PUBLISH_PROVIDER 为空）",
  hint: "要用发布能力：设 STEPWORK_PUBLISH_PROVIDER=opencli 并安装 opencli；不用就保持为空 —— 生成填充包（publish fill）不受影响",
  exit_code: null,
  auto_publish: false,
};

/** 真机输出：配了 opencli 但机器上没装。 */
const UNAVAILABLE_NOT_INSTALLED = {
  state: "unavailable",
  provider: "opencli",
  detail: "opencli 不在 PATH 上，无法把内容填进平台表单",
  hint: "要发布能力就装它：npm i -g @jackwener/opencli（需要 Node ≥ 20.18.1），再起它的 daemon 并装浏览器扩展；不需要就把 STEPWORK_PUBLISH_PROVIDER 留空（本能力默认关闭）",
  exit_code: null,
  auto_publish: false,
};

/** 按契约构造：通道就绪（`classify_exit` 把 0 判为 ready）。 */
const READY = {
  state: "ready",
  provider: "opencli",
  detail: "opencli doctor 退出码 0：ok",
  hint: "可以填充；填完停在预览页，最终点发布仍由你手动完成（ADR-008）",
  exit_code: 0,
  auto_publish: false,
};

/** 按契约构造：装了、桥也通，但浏览器会话未登录（`EX_NOPERM` = 77）。 */
const NEED_LOGIN = {
  state: "need_login",
  provider: "opencli",
  detail: "opencli doctor 退出码 77：not logged in",
  hint: "在它的浏览器扩展里登录目标平台，然后重跑本命令",
  exit_code: 77,
  auto_publish: false,
};

describe("toProviderStatus", () => {
  it("读的是 snake_case 契约（exit_code / auto_publish 那一层）", () => {
    const v = toProviderStatus(NEED_LOGIN);
    expect(v.state).toBe("need_login");
    expect(v.provider).toBe("opencli");
    expect(v.exitCode).toBe(77);
  });

  it("未知 state 落 unknown，但**原始值保留下来**（不吞）", () => {
    const v = toProviderStatus({ ...READY, state: "mostly_ready" });
    expect(v.state).toBe("unknown");
    expect(v.rawState).toBe("mostly_ready");
  });

  it("detail 缺失 / 不是对象时不炸，退化成空的 unknown", () => {
    for (const bad of [null, undefined, "oops", 42, []]) {
      const v = toProviderStatus(bad);
      expect(v.state).toBe("unknown");
      expect(v.provider).toBe("");
      expect(v.exitCode).toBeNull();
    }
  });

  it("exit_code 为 null（没装 / 超时被杀）读成 null，绝不当成 0", () => {
    expect(toProviderStatus(UNAVAILABLE_NOT_INSTALLED).exitCode).toBeNull();
  });
});

describe("stateBadge / isUsable —— 红线：只有 ready 算可用", () => {
  it("三态各自的标", () => {
    expect(stateBadge(toProviderStatus(READY)).label).toBe("可填充");
    expect(stateBadge(toProviderStatus(NEED_LOGIN)).label).toBe("需要登录");
    expect(stateBadge(toProviderStatus(UNAVAILABLE_UNSET)).label).toBe("不可用");
  });

  it("未识别的状态值一律**不**可用，且照实显示原始值", () => {
    // 这条挡的是「黑名单写法」：非 unavailable 就当可用，会把后端新增的
    // 任何中间态直接放行，而中间态恰恰是最不该被当成能用的那一类
    const v = toProviderStatus({ ...READY, state: "partially_ready" });
    expect(isUsable(v)).toBe(false);
    expect(stateBadge(v).tone).toBe("danger");
    expect(stateBadge(v).label).toContain("partially_ready");
  });

  it("ready 才可用，另两态都不可用", () => {
    expect(isUsable(toProviderStatus(READY))).toBe(true);
    expect(isUsable(toProviderStatus(NEED_LOGIN))).toBe(false);
    expect(isUsable(toProviderStatus(UNAVAILABLE_UNSET))).toBe(false);
  });
});

describe("MANUAL_PUBLISH_NOTICE —— 红线：无条件成立", () => {
  it("是常量而非函数：没有输入可依赖，也就没有「某种情况下忘了显示」", () => {
    expect(typeof MANUAL_PUBLISH_NOTICE).toBe("string");
    expect(MANUAL_PUBLISH_NOTICE.length).toBeGreaterThan(0);
    // 必须点出「手动」这件事本身，而不只是暗示
    expect(MANUAL_PUBLISH_NOTICE).toContain("手动");
  });
});

describe("fixHint —— 红线：两种不可用的修法必须分开", () => {
  it("后端给了 hint 就用它（只有它知道是「没装」还是「daemon 没起」）", () => {
    const v = toProviderStatus(UNAVAILABLE_NOT_INSTALLED);
    expect(fixHint(v)).toContain("npm i -g @jackwener/opencli");
  });

  it("need_login 与 unavailable 的兜底文案必须不同", () => {
    const login = fixHint(toProviderStatus({ ...NEED_LOGIN, hint: "", detail: "" }));
    const down = fixHint(
      toProviderStatus({ ...UNAVAILABLE_UNSET, hint: "", detail: "" }),
    );
    expect(login).not.toBe(down);
    expect(login).toContain("登录");
    expect(down).toContain("安装");
  });

  it("hint 为空时回落到 detail，不显示空白", () => {
    const v = toProviderStatus({ ...UNAVAILABLE_UNSET, hint: "" });
    expect(fixHint(v)).toBe(UNAVAILABLE_UNSET.detail);
  });

  it("hint 与 detail 都为空时仍给出可执行文案（不是空串）", () => {
    for (const state of ["ready", "need_login", "unavailable", "weird"]) {
      const v = toProviderStatus({ state, provider: "", detail: "", hint: "" });
      expect(fixHint(v).length).toBeGreaterThan(0);
    }
  });
});

describe("diagnosticsLine", () => {
  it("就绪时不显示（正常工作的通道不需要用户看退出码）", () => {
    expect(diagnosticsLine(toProviderStatus(READY))).toBeNull();
  });

  it("不可用且拿到退出码时显示，带得上码", () => {
    expect(diagnosticsLine(toProviderStatus(NEED_LOGIN))).toContain("77");
  });

  it("拿不到退出码时（没装 / 超时被杀）不显示半个诊断行", () => {
    expect(diagnosticsLine(toProviderStatus(UNAVAILABLE_NOT_INSTALLED))).toBeNull();
  });
});
