/** vitest 全局 setup：jest-dom 断言 + 每例后清理副作用。 */
import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
  // 多数 lib 把工作区 id 存在 localStorage，用例间必须互不污染
  globalThis.localStorage?.clear();
});
