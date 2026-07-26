/**
 * 由 scripts/gen_result_types.py 从 worker/runtime/results/models.py 生成。
 *
 * 请勿手改：改后端模型后重跑生成脚本。CI 会校验两者同步。
 *
 * 这层类型的意义：此前前端用 `as { xxx?: T }` 裸断言消费 detail，后端改字段名
 * 前端只会静默拿到 undefined —— 没有编译错误、没有测试变红。现在改名会直接
 * 编译不过。
 */

/* eslint-disable */


export interface AddA2aAgentDetail {
  connection_id: string;
  agent_name: string;
  url: string;
  skills: Record<string, unknown>[];
  token_persisted: boolean;
}

export interface AddAcpAgentDetail {
  connection_id: string;
  agent_info: Record<string, unknown>;
}

export interface AddMcpServerDetail {
  connection_id: string;
  name: string;
  server_info: Record<string, unknown>;
  tools: Record<string, unknown>[];
}

export interface BuildPlatformFillPackageDetail {
  fill_package: Record<string, unknown>;
}

export interface CallA2aSkillDetail {
  agent_task_id: string;
  skill: string;
  text: string;
  remote_state?: string | null;
  trust_level: string;
  review_state: string;
}

export interface CallMcpToolDetail {
  agent_task_id: string;
  tool: string;
  text: string;
  trust_level: string;
  review_state: string;
  is_error: boolean;
}

export interface CancelScheduledPublishDetail {
  cancelled: string;
}

export interface CreatePlatformVariantDetail {
  variant: Record<string, unknown>;
}

export interface DeleteAgentConnectionDetail {
  deleted: string;
}

export interface EndAcpSessionDetail {
  ended: boolean;
}

export interface ExportBundleDetail {
  bundle_path: string;
  variant_id: string;
  files: Record<string, string | null>;
}

export interface ExportEditTimelineDetail {
  path: string;
  format: string;
  scene_count: number;
  marker_count: number;
  note: string;
}

export interface FireDueSchedulesDetail {
  fired: Record<string, unknown>[];
  count: number;
}

export interface GetA2aServerStatusDetail {
  running: boolean;
  url: string;
}

export interface GetAgentCardDetail {
  card: Record<string, unknown>;
}

export interface GetAgentTaskDetail {
  task: Record<string, unknown>;
}

export interface ListAcpSessionsDetail {
  sessions: Record<string, unknown>[];
}

export interface ListAgentArtifactsDetail {
  artifacts: Record<string, unknown>[];
  note: string;
}

export interface ListAgentConnectionsDetail {
  connections: Record<string, unknown>[];
}

export interface ListAgentTasksDetail {
  tasks: Record<string, unknown>[];
  note: string;
}

export interface ListMcpToolsDetail {
  connection_id: string;
  tools: Record<string, unknown>[];
}

export interface ListPlatformVariantsDetail {
  variants: Record<string, unknown>[];
}

export interface ListPublishJobsDetail {
  publish_jobs: Record<string, unknown>[];
}

export interface ListScheduledPublishesDetail {
  scheduled: Record<string, unknown>[];
}

export interface RecordPublishResultDetail {
  publish_job: Record<string, unknown>;
}

export interface RequestPublishAuthorizationDetail {
  approval_id: string;
  publish_job_id: string;
  content_hash: string;
  state: string;
}

export interface SchedulePublishDetail {
  id: string;
  mode: string;
  scheduled_at: string;
  platform: string;
  platform_label: string;
  unattended: boolean;
  note: string;
  issues: Record<string, unknown>[];
  status: string;
  mode_description: string;
}

export interface SendAcpPromptDetail {
  agent_task_id: string;
  stop_reason?: string | null;
  text: string;
  updates: Record<string, unknown>[];
  pending_approvals: number;
  trust_level: string;
  review_state: string;
}

export interface SetAgentConnectionStatusDetail {
  connection: Record<string, unknown>;
}

export interface StartA2aServerDetail {
  running: boolean;
  url: string;
  card_url: string;
  token: string;
}

export interface StartAcpSessionDetail {
  session_id: string;
  external_session_id: string;
  root: string;
  project_id: string;
}

export interface StopA2aServerDetail {
  stopped: boolean;
  running: boolean;
}

/**
 * commandType → detail 类型的映射。dispatchCommandTyped 用它做推导；
 * 未登记契约的命令回落 Record<string, unknown>（与改造前行为一致）。
 */
export interface CommandResultDetails {
  AddA2aAgent: AddA2aAgentDetail;
  AddAcpAgent: AddAcpAgentDetail;
  AddMcpServer: AddMcpServerDetail;
  BuildPlatformFillPackage: BuildPlatformFillPackageDetail;
  CallA2aSkill: CallA2aSkillDetail;
  CallMcpTool: CallMcpToolDetail;
  CancelScheduledPublish: CancelScheduledPublishDetail;
  CreatePlatformVariant: CreatePlatformVariantDetail;
  DeleteAgentConnection: DeleteAgentConnectionDetail;
  EndAcpSession: EndAcpSessionDetail;
  ExportBundle: ExportBundleDetail;
  ExportEditTimeline: ExportEditTimelineDetail;
  FireDueSchedules: FireDueSchedulesDetail;
  GetA2aServerStatus: GetA2aServerStatusDetail;
  GetAgentCard: GetAgentCardDetail;
  GetAgentTask: GetAgentTaskDetail;
  ListAcpSessions: ListAcpSessionsDetail;
  ListAgentArtifacts: ListAgentArtifactsDetail;
  ListAgentConnections: ListAgentConnectionsDetail;
  ListAgentTasks: ListAgentTasksDetail;
  ListMcpTools: ListMcpToolsDetail;
  ListPlatformVariants: ListPlatformVariantsDetail;
  ListPublishJobs: ListPublishJobsDetail;
  ListScheduledPublishes: ListScheduledPublishesDetail;
  RecordPublishResult: RecordPublishResultDetail;
  RequestPublishAuthorization: RequestPublishAuthorizationDetail;
  SchedulePublish: SchedulePublishDetail;
  SendAcpPrompt: SendAcpPromptDetail;
  SetAgentConnectionStatus: SetAgentConnectionStatusDetail;
  StartA2aServer: StartA2aServerDetail;
  StartAcpSession: StartAcpSessionDetail;
  StopA2aServer: StopA2aServerDetail;
}

/** 已登记响应契约的命令名（运行期可用，测试据此核对覆盖面）。 */
export const CONTRACTED_COMMANDS = [
  "AddA2aAgent",
  "AddAcpAgent",
  "AddMcpServer",
  "BuildPlatformFillPackage",
  "CallA2aSkill",
  "CallMcpTool",
  "CancelScheduledPublish",
  "CreatePlatformVariant",
  "DeleteAgentConnection",
  "EndAcpSession",
  "ExportBundle",
  "ExportEditTimeline",
  "FireDueSchedules",
  "GetA2aServerStatus",
  "GetAgentCard",
  "GetAgentTask",
  "ListAcpSessions",
  "ListAgentArtifacts",
  "ListAgentConnections",
  "ListAgentTasks",
  "ListMcpTools",
  "ListPlatformVariants",
  "ListPublishJobs",
  "ListScheduledPublishes",
  "RecordPublishResult",
  "RequestPublishAuthorization",
  "SchedulePublish",
  "SendAcpPrompt",
  "SetAgentConnectionStatus",
  "StartA2aServer",
  "StartAcpSession",
  "StopA2aServer",
] as const;
