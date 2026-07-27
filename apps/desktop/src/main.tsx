import React from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "./styles/tokens.css";
import "./styles/global.css";

const container = document.getElementById("root");
if (!container) {
  throw new Error("Root element #root not found");
}

/**
 * 命令调用的查询客户端（见 lib/useCommand.ts）。
 *
 * 默认值按「本地 IPC」而非「远程 API」调：
 * - 不自动重试：命令失败基本是业务性拒绝（权限不足、状态不对），重试只会
 *   重复失败并拖慢用户拿到错误提示的时间；
 * - 窗口重新聚焦不自动重取：桌面端来回切窗口很频繁，无谓刷新会打断操作。
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false, refetchOnWindowFocus: false },
    mutations: { retry: false },
  },
});

createRoot(container).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
);
