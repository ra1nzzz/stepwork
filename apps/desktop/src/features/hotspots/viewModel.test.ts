/**
 * 热点面板判据层的 L1 测试（S6 前端热点面板的护栏）。
 *
 * 夹具**直接取自真机 CLI 输出**（`.workbuddy/hotspot-acceptance/cli-recommend.json`
 * 与 `cli-convert.json`）——手搓的夹具会把「我以为契约长什么样」当成契约，
 * 那正是这类断裂的成因。形状变了这里先红，而不是等用户看到空白面板。
 *
 * 每一条断言都对应一条**产品红线**，不是覆盖率凑数：
 *   降级必须可见 · 退化必须提示 · 外部素材必须标待复核 · 空态必须给解释
 */

import { describe, expect, it } from "vitest";
import {
  brandGateNotice,
  breakdownRows,
  briefReasonLabel,
  candidateLine,
  emptyHint,
  nextStepHint,
  reasonBadge,
  reusedNotice,
  SCORE_FORMULA,
  toBriefView,
  toRecommendView,
  trustBadge,
} from "./viewModel";

/** 真机 `hotspots recommend` 出参（2026-09-10，8 条候选挑出 3 条，无品牌档）。 */
const RECOMMEND_DETAIL = {
  recommendations: [
    {
      id: "4757cac91b3a9961",
      source: "toutiao_hot",
      title: "青岛货轮火灾25人遇难",
      url: "https://www.toutiao.com/trending/7683415450185220150/",
      summary: "青岛货轮火灾25人遇难",
      publishedAt: "2026-09-10T14:41:20.741465+00:00",
      score: 47.13,
      meta: { label: "recentProgress", rank: 1 },
      batchId: "hsb_e1505c01de4f",
      discoveredAt: "2026-09-10T14:41:47.676680+00:00",
      breakdown: {
        freshness: 0.9997,
        heat: 0.5,
        brandFit: 0.5,
        novelty: 1.0,
        feedback: 0.5,
        penalty: 0.0,
      },
      reason: "青岛货轮火灾25人遇难：时效新。",
      reasonSource: "rule",
      risks: [],
    },
  ],
  count: 3,
  considered: 8,
  reason_source: "rule",
  brand_applied: false,
};

/** 真机 `hotspots convert` 出参。 */
const CONVERT_DETAIL = {
  content_version_id: "cv_d695289eb6a84559be2ee2ac3665b726",
  hotspot_id: "4757cac91b3a9961",
  source: "toutiao_hot",
  title: "青岛货轮火灾25人遇难",
  url: "https://www.toutiao.com/trending/7683415450185220150/",
  trust_level: "external-unverified",
  review_state: "pending_review",
  reason_source: "rule",
  breakdown_attached: true,
  reused: false,
  brief: "【外部热点素材 · 未经事实核实】\n以下内容摘自公开热榜……",
  next_step: {
    command: "GenerateTopic",
    payload: { source_version_id: "cv_d695289eb6a84559be2ee2ac3665b726" },
  },
};

describe("读取契约：两套口径（顶层 snake_case / 条目 camelCase）", () => {
  it("真机 recommend 出参能被正确读出", () => {
    const v = toRecommendView(RECOMMEND_DETAIL);
    expect(v.reasonSource).toBe("rule");
    expect(v.brandApplied).toBe(false);
    expect(v.considered).toBe(8);
    expect(v.count).toBe(3);
    expect(v.recommendations).toHaveLength(1);
    expect(v.recommendations[0]?.reasonSource).toBe("rule");
    expect(v.recommendations[0]?.breakdown.brandFit).toBe(0.5);
    expect(v.recommendations[0]?.score).toBe(47.13);
  });

  it("reason_note 缺省时回落成空串，而不是 undefined 漏进渲染", () => {
    // 真机输出里就**没有** reason_note 这个键（只在降级时才有）
    expect(toRecommendView(RECOMMEND_DETAIL).reasonNote).toBe("");
  });

  it("缺少字段不炸，且不产生 undefined", () => {
    const v = toRecommendView({});
    expect(v.recommendations).toEqual([]);
    expect(v.count).toBe(0);
    expect(v.reasonSource).toBe("");
    expect(v.reasonNote).toBe("");
  });

  it("recommendations 不是数组时也不炸", () => {
    expect(toRecommendView({ recommendations: null }).recommendations).toEqual([]);
    expect(toRecommendView({ recommendations: "oops" }).recommendations).toEqual([]);
  });

  it("真机 convert 出参能被正确读出，且 next_step 原样带出来", () => {
    const v = toBriefView(CONVERT_DETAIL);
    expect(v.contentVersionId).toBe("cv_d695289eb6a84559be2ee2ac3665b726");
    expect(v.trustLevel).toBe("external-unverified");
    expect(v.reviewState).toBe("pending_review");
    expect(v.reused).toBe(false);
    expect(v.breakdownAttached).toBe(true);
    expect(v.nextStep?.command).toBe("GenerateTopic");
  });

  it("没有 next_step 时给 null，不给一个空壳对象", () => {
    expect(toBriefView({}).nextStep).toBeNull();
  });
});

describe("红线一：理由降级必须可见", () => {
  it("rule 与 ai 必须给出不同的标，且 rule 的说明里点名 AI 未参与", () => {
    const ai = reasonBadge("ai");
    const rule = reasonBadge("rule");
    expect(rule.label).not.toBe(ai.label);
    expect(rule.label).not.toContain("AI");
    expect(rule.hint).toContain("AI 未参与");
  });

  it("未知来源不静默回落到 ai/rule，而是明说来源未知", () => {
    const unknown = reasonBadge("something-new");
    expect(unknown.label).toBe("来源未知");
    expect(unknown.hint).toContain("something-new");
  });

  it("简报侧 none 必须说明「调用方没带」而不是显示成无理由", () => {
    const text = briefReasonLabel("none");
    expect(text).not.toBe("");
    expect(text).toContain("调用方没带");
    expect(briefReasonLabel("rule")).not.toBe(text);
  });
});

describe("红线二：品牌闸门退化必须提示", () => {
  it("未绑定品牌档时给出提示，并点名退化成「热 + 新」", () => {
    const notice = brandGateNotice(false);
    expect(notice).not.toBeNull();
    expect(notice).toContain("品牌档");
    expect(notice).toContain("热 + 新");
  });

  it("绑了品牌档就不打扰", () => {
    expect(brandGateNotice(true)).toBeNull();
  });
});

describe("红线三：外部未核实素材必须挂标", () => {
  it("external-unverified → 待复核", () => {
    expect(trustBadge("external-unverified")).toBe("待复核");
  });

  it("没有信任等级就不挂牌（内部素材不背这个标）", () => {
    expect(trustBadge("")).toBeNull();
  });

  it("出现新等级时挂标并回显原值，不静默当成已核实", () => {
    expect(trustBadge("partner-supplied")).toBe("待复核（partner-supplied）");
  });
});

describe("红线四：空态必须给解释，不能是空白面板", () => {
  it("没数据 与 被筛掉 是两种解释", () => {
    const none = emptyHint(0);
    const filtered = emptyHint(8);
    expect(none).toContain("抓取热点");
    expect(filtered).toContain("8");
    expect(filtered).not.toContain("抓取热点");
  });

  it("推荐必须带上分母（下判断要让人看见范围）", () => {
    const line = candidateLine(3, 8);
    expect(line).toContain("3");
    expect(line).toContain("8");
  });

  it("复用时必须说明没新增版本", () => {
    expect(reusedNotice(true)).toContain("复用");
    expect(reusedNotice(false)).toBeNull();
  });
});

describe("红线五：打分口径不能被展示成加总", () => {
  it("公式是乘性的，文案里不能出现加号", () => {
    expect(SCORE_FORMULA).toContain("×");
    expect(SCORE_FORMULA).not.toContain("+");
  });

  it("品牌契合归类为闸门而不是核心项", () => {
    const rows = breakdownRows({
      freshness: 0.9997,
      heat: 0.5,
      brandFit: 0.5,
      novelty: 1,
      feedback: 0.5,
      penalty: 0,
    });
    expect(rows.filter((r) => r.kind === "core")).toHaveLength(4);
    expect(rows.filter((r) => r.kind === "gate").map((r) => r.key)).toEqual(["brandFit"]);
    expect(rows.filter((r) => r.kind === "penalty").map((r) => r.key)).toEqual(["penalty"]);
  });

  it("百分比取整且不出现 NaN", () => {
    const rows = breakdownRows({
      freshness: 0.9997,
      heat: 0,
      brandFit: 0,
      novelty: 0,
      feedback: 0,
      penalty: 0,
    });
    expect(rows[0]?.percent).toBe(100);
    expect(rows.every((r) => Number.isFinite(r.percent))).toBe(true);
  });
});

describe("红线六：下一步的 payload 键名必须照契约，不让 UI 手写", () => {
  it("GenerateTopic 拿到的键是 source_version_id（不是 sourceVersionId）", () => {
    const view = toBriefView(CONVERT_DETAIL);
    expect(view.nextStep?.payload).toHaveProperty("source_version_id");
    expect(view.nextStep?.payload).not.toHaveProperty("sourceVersionId");
  });

  it("提示里回显真实键名，而不是一个泛化的「下一步」", () => {
    const hint = nextStepHint(toBriefView(CONVERT_DETAIL).nextStep);
    expect(hint).toContain("GenerateTopic");
    expect(hint).toContain("source_version_id");
  });

  it("没有下一步时返回 null，不渲染空动作", () => {
    expect(nextStepHint(null)).toBeNull();
  });
});
