/**
 * 命令调用的统一数据层（基于 react-query）。
 *
 * 改造前：79 处手写「dispatch → setLoading → try/catch → setError → setData」，
 * 其中 **10 处根本没检查 `res.ok`** —— 后端拒了，UI 照常刷新、显示旧状态、
 * 一句提示都没有。用户点「停用连接」没反应，也不知道为什么。
 *
 * 这里的关键设计是**不给静默失败留出口**：`res.ok === false` 一律抛成异常进
 * error 通道。调用方要么渲染 error，要么让它冒泡 —— 但不可能「忘了检查」，
 * 因为根本没有「检查」这个步骤可以忘。
 *
 * 配合 `results.generated.ts`，`data` 是按 commandType 推导的强类型。
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { buildEnvelope, dispatchCommand, getWorkspaceId } from "./tauri";
import type { CommandEnvelope, TypedCommandResult } from "./types";

type CommandType = CommandEnvelope["commandType"];

/** 命令返回 ok=false 时抛出，携带后端错误码与 detail 供 UI 展示。 */
export class CommandError extends Error {
  readonly code: string;
  readonly detail: unknown;

  constructor(code: string, detail: unknown) {
    super(code);
    this.name = "CommandError";
    this.code = code;
    this.detail = detail;
  }
}

/** detail 的推导类型（未登记契约的命令回落 Record<string, unknown>）。 */
export type DetailOf<K extends CommandType> = NonNullable<
  TypedCommandResult<K>["detail"]
>;

/**
 * 执行一条命令并把失败转成异常。
 *
 * 这是本模块唯一与 worker 通信的地方 —— 所有「ok 检查」集中在此一处，
 * 而不是散落在 79 个调用点各写一遍（那必然会漏，实际就漏了 10 处）。
 */
export async function runCommand<K extends CommandType>(
  commandType: K,
  payload: Record<string, unknown> = {},
  options: { projectId?: string | null; idempotencyKey?: string | null } = {},
): Promise<DetailOf<K>> {
  const envelope = buildEnvelope(
    commandType,
    getWorkspaceId(),
    options.projectId ?? null,
    payload,
    options.idempotencyKey ?? null,
  ) as CommandEnvelope & { commandType: K };

  const res = (await dispatchCommand(envelope)) as TypedCommandResult<K>;
  if (!res.ok) {
    throw new CommandError(res.error ?? "COMMAND_FAILED", res.detail);
  }
  return (res.detail ?? {}) as DetailOf<K>;
}

/** 查询类命令的 key：命令名 + 工作区 + payload，payload 变了自动重取。 */
export function commandKey(
  commandType: CommandType,
  payload: Record<string, unknown> = {},
  projectId?: string | null,
): unknown[] {
  return [commandType, getWorkspaceId(), projectId ?? null, payload];
}

export interface UseCommandOptions {
  /** false 时不发起请求（如尚未选择项目） */
  enabled?: boolean;
  projectId?: string | null;
  /** 毫秒；默认 0 = 每次挂载都重取（本地 IPC 很便宜，宁可新鲜） */
  staleTime?: number;
}

/**
 * 读取类命令。
 *
 * 默认 `staleTime: 0`：这是本地 IPC 不是网络请求，重取成本极低，
 * 而拿到过期数据（比如刚改完设置还显示旧值）是实打实的体验问题。
 */
export function useCommand<K extends CommandType>(
  commandType: K,
  payload: Record<string, unknown> = {},
  options: UseCommandOptions = {},
): UseQueryResult<DetailOf<K>, CommandError> {
  const { enabled = true, projectId = null, staleTime = 0 } = options;
  return useQuery<DetailOf<K>, CommandError>({
    queryKey: commandKey(commandType, payload, projectId),
    queryFn: () => runCommand(commandType, payload, { projectId }),
    enabled,
    staleTime,
    // 命令失败基本是业务性拒绝（权限、状态不对），重试只会重复失败并拖慢反馈
    retry: false,
  });
}

/**
 * 写入类命令。
 *
 * `invalidates` 列出成功后需要失效的命令 —— 例如停用连接后
 * `ListAgentConnections` 应重取。手写代码里这一步靠各处记得调 `loadXxx()`，
 * 漏了就是「操作成功但列表没变」。
 */
export function useCommandMutation<K extends CommandType>(
  commandType: K,
  options: { invalidates?: CommandType[]; projectId?: string | null } = {},
): UseMutationResult<DetailOf<K>, CommandError, Record<string, unknown>> {
  const client = useQueryClient();
  const { invalidates = [], projectId = null } = options;
  return useMutation<DetailOf<K>, CommandError, Record<string, unknown>>({
    mutationFn: (payload) => runCommand(commandType, payload, { projectId }),
    onSuccess: () => {
      for (const target of invalidates) {
        // 按命令名前缀失效：同一命令的不同 payload 变体一并重取
        void client.invalidateQueries({ queryKey: [target] });
      }
    },
  });
}

/** 把 CommandError 转成可展示文案（非 CommandError 也能兜住）。 */
export function errorText(error: unknown): string {
  if (error instanceof CommandError) return error.code;
  if (error instanceof Error) return error.message;
  return String(error ?? "未知错误");
}
