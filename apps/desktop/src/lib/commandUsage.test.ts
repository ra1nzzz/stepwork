/**
 * 「每个 `dispatchCommand` 调用点都读过 `.ok`」—— 源码形状断言。
 *
 * 为什么值得一条测试：`ok: false` 被忽略 = 后端拒了、UI 照常刷新、一句提示都没有。
 * 用户点「停用连接」没反应，也不知道为什么。这类缺陷**行为测试很难覆盖**
 * （要测得为每条命令写一个「后端拒绝」用例），但它能靠源码形状一句话说清：
 * 拿到返回值就得读 `.ok`。
 *
 * 与 `tauri.test.ts` 同构：直接读源码，不在测试里抄一份副本。
 *
 * ⚠️ 本文件最重要的部分是 `检测器自检` —— 光有「跑一遍是绿的」证明不了护栏
 * 有效（可能它压根什么都没匹配上）。所以用合成源码把检测器本身钉住。
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { resolve, relative, join } from "node:path";

const SRC_ROOT = resolve(process.cwd(), "src");
/** 拿到变量后，向后看多少行算「检查过」 */
const WINDOW = 50;

/**
 * 允许「把检查转交出去」的文件 —— 每个都必须写出理由。
 *
 * 白名单不是「这些文件不重要」，而是「它们的返回值注定要交给别人判」：
 * 在这两处强求 `.ok` 只会逼出无意义的检查（通信层不知道业务上该怎么反应）。
 */
const DELEGATING = new Map<string, string>([
  ["lib/tauri.ts", "通信实现层：原样交出 CommandResult，检查责任在上层"],
  ["lib/useCommand.ts", "全仓唯一集中检查点：runCommand 内 `if (!res.ok) throw`"],
]);

/** 行内豁免标记：`// ok-check: <理由>` */
const EXEMPT = /\/\/\s*ok-check\s*:/;

export interface Violation {
  line: number;
  detail: string;
}

interface Group {
  /** 组内变量名 */
  vars: string[];
  /** 声明行（1-based） */
  start: number;
  /** 块结束行（1-based） */
  end: number;
}

const DISPATCH = /dispatchCommand\s*\(/;
const DEFINITION = /function\s+dispatchCommand\s*\(/;
/** `const [a, b] = await Promise.all([ ...` （可能跨行） */
const PROMISE_ALL = /const\s*\[([^\]]*)\]\s*=\s*await\s+Promise\.all\(\s*\[/g;
/** `const res = (await dispatchCommand(` / `const res = await dispatchCommand(` */
const BOUND =
  /(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]*)?=\s*\(*\s*(?:await\s+)?dispatchCommand\s*\(/;
/** `return (await dispatchCommand(` —— 直接交出去，调用方再也拿不到控制权 */
const RETURNED = /\breturn\b[^;]*dispatchCommand\s*\(/;

function lineOf(source: string, index: number): number {
  let n = 1;
  for (let i = 0; i < index && i < source.length; i += 1) {
    if (source[i] === "\n") n += 1;
  }
  return n;
}

/** 找到 `Promise.all([` 所属的 `]` 位置（简单括号配平）。 */
function closeBracket(source: string, openIdx: number): number {
  let depth = 0;
  for (let i = openIdx; i < source.length; i += 1) {
    const ch = source[i];
    if (ch === "[" || ch === "(") depth += 1;
    else if (ch === "]" || ch === ")") {
      depth -= 1;
      if (depth === 0) return i;
    }
  }
  return source.length - 1;
}

/** 单变量是否在 [from, from+WINDOW) 行窗口内被读过 `.ok`。 */
function readsOk(lines: string[], varName: string, from: number): boolean {
  const re = new RegExp(`\\b${varName.replace(/\$/g, "\\$")}\\s*\\.\\s*ok\\b`);
  const end = Math.min(lines.length, from + WINDOW);
  for (let i = from; i < end; i += 1) {
    if (re.test(lines[i] ?? "")) return true;
  }
  return false;
}

/**
 * 找出「拿到 dispatchCommand 返回值却没读 .ok」的调用点。
 *
 * 导出是为了让「检测器自检」能喂合成源码进来 —— 这是本文件存在意义的另一半。
 */
export function findUncheckedCalls(
  relPath: string,
  source: string,
): Violation[] {
  if (DELEGATING.has(relPath)) return [];
  const lines = source.split(/\r?\n/);
  const exempted = new Set<number>();
  lines.forEach((line, i) => {
    if (EXEMPT.test(line)) exempted.add(i + 1);
  });

  const out: Violation[] = [];
  /** 已被 Promise.all 解构覆盖的 dispatch 行号 */
  const covered = new Set<number>();
  const groups: Group[] = [];

  // ---- 第一遍：Promise.all 解构 ----
  PROMISE_ALL.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = PROMISE_ALL.exec(source)) !== null) {
    const vars = (m[1] ?? "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const start = lineOf(source, m.index);
    const bodyOpen = source.indexOf("[", m.index + m[0].length - 1);
    const end = lineOf(source, closeBracket(source, bodyOpen));
    for (let i = start; i <= end; i += 1) {
      if (DISPATCH.test(lines[i - 1] ?? "")) covered.add(i);
    }
    groups.push({ vars, start, end });
  }

  for (const g of groups) {
    for (const v of g.vars) {
      if (!readsOk(lines, v, g.start)) {
        out.push({
          line: g.start,
          detail: `Promise.all 解构出的 \`${v}\`（第 ${g.start} 行起）从未读过 \`.ok\``,
        });
      }
    }
  }

  // ---- 第二遍：其余调用点 ----
  lines.forEach((line, idx) => {
    const ln = idx + 1;
    if (!DISPATCH.test(line) || DEFINITION.test(line)) return;
    if (covered.has(ln) || exempted.has(ln)) return;

    const bound = BOUND.exec(line);
    if (bound) {
      const v = bound[1] ?? "";
      if (!readsOk(lines, v, ln)) {
        out.push({ line: ln, detail: `\`${v}\` 从未读过 \`.ok\`` });
      }
      return;
    }
    if (RETURNED.test(line)) {
      out.push({ line: ln, detail: "直接 return 结果，调用方无从检查 `.ok`" });
      return;
    }
    out.push({
      line: ln,
      detail: "返回值没有绑定到变量，等于放弃检查 `.ok`",
    });
  });

  return out.sort((a, b) => a.line - b.line);
}

function walk(dir: string, acc: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name.startsWith(".")) continue;
      walk(full, acc);
    } else if (/\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) {
      acc.push(full);
    }
  }
  return acc;
}

describe("检测器自检（护栏会不会响）", () => {
  it("漏检的调用点会被抓住，且点名变量", () => {
    const src = [
      "async function f() {",
      "  const res = await dispatchCommand(env);",
      "  setData(res.detail);",
      "}",
    ].join("\n");
    const got = findUncheckedCalls("features/x.tsx", src);
    expect(got).toHaveLength(1);
    expect(got[0]?.line).toBe(2);
    expect(got[0]?.detail).toContain("res");
  });

  it("已读 .ok 的调用点不报", () => {
    const src = [
      "async function f() {",
      "  const res = await dispatchCommand(env);",
      "  if (!res.ok) { setError(res.error); return; }",
      "  setData(res.detail);",
      "}",
    ].join("\n");
    expect(findUncheckedCalls("features/x.tsx", src)).toEqual([]);
  });

  it("Promise.all 解构逐个变量检查，漏一个就报", () => {
    const src = [
      "async function f() {",
      "  const [a, b] = await Promise.all([",
      "    dispatchCommand(envA),",
      "    dispatchCommand(envB),",
      "  ]);",
      "  if (a.ok) setA(a.detail);",
      "  setB(b.detail);",
      "}",
    ].join("\n");
    const got = findUncheckedCalls("features/x.tsx", src);
    expect(got).toHaveLength(1);
    expect(got[0]?.detail).toContain("b");
  });

  it("直接 return 与丢弃返回值都算漏检", () => {
    const src = [
      "async function f() {",
      "  return dispatchCommand(env);",
      "}",
      "async function g() {",
      "  await dispatchCommand(env);",
      "}",
    ].join("\n");
    const got = findUncheckedCalls("features/x.tsx", src);
    expect(got.map((v) => v.line)).toEqual([2, 5]);
  });

  it("函数定义行不算调用点；豁免标记生效", () => {
    const src = [
      "export async function dispatchCommand(env) {",
      "  return delegate(env);",
      "}",
      "async function f() {",
      "  const r = await dispatchCommand(env); // ok-check: 交给 adapt() 判",
      "  adapt(r);",
      "}",
    ].join("\n");
    expect(findUncheckedCalls("features/x.tsx", src)).toEqual([]);
  });

  it("白名单文件整体豁免", () => {
    const src = "const r = await dispatchCommand(env);";
    expect(findUncheckedCalls("lib/useCommand.ts", src)).toEqual([]);
    expect(findUncheckedCalls("features/x.tsx", src)).toHaveLength(1);
  });
});

describe("真实源码：dispatchCommand 调用点没有静默失败出口", () => {
  const files = walk(SRC_ROOT).sort();

  it("扫描范围非空（防止 walk 静默返回空数组导致假绿）", () => {
    expect(files.length).toBeGreaterThan(20);
  });

  it("真实源码里确实有大量调用点（防止「什么都没匹配上」的假绿）", () => {
    let count = 0;
    for (const file of files) {
      count += (readFileSync(file, "utf-8").match(/dispatchCommand\s*\(/g) ?? [])
        .length;
    }
    // 2026-09-13 实测 76 处（含定义行与两个白名单文件）。取 60 留出重构余量，
    // 但绝不接受「几乎扫不到」——那是这条测试失效的典型样子
    expect(count).toBeGreaterThanOrEqual(60);
  });

  it("两个白名单文件都还在（防止白名单腐烂成空话）", () => {
    for (const rel of DELEGATING.keys()) {
      expect(
        files.some((f) => relative(SRC_ROOT, f).replace(/\\/g, "/") === rel),
        `白名单文件 ${rel} 不存在了，请更新 DELEGATING`,
      ).toBe(true);
    }
  });

  it("每个调用点都读了 .ok，或有显式豁免", () => {
    const problems: string[] = [];
    for (const file of files) {
      const rel = relative(SRC_ROOT, file).replace(/\\/g, "/");
      for (const v of findUncheckedCalls(rel, readFileSync(file, "utf-8"))) {
        problems.push(`${rel}:${v.line}  ${v.detail}`);
      }
    }
    expect(problems, `\n${problems.join("\n")}\n`).toEqual([]);
  });
});
