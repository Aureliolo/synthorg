/** TypeScript interfaces mirroring backend Pydantic DTOs and domain models. */

// ── Enums ────────────────────────────────────────────────────

export type TaskStatus =
  | 'created'
  | 'assigned'
  | 'in_progress'
  | 'in_review'
  | 'completed'
  | 'blocked'
  | 'failed'
  | 'interrupted'
  | 'cancelled'

export type TaskType =
  | 'development'
  | 'design'
  | 'research'
  | 'review'
  | 'meeting'
  | 'admin'

export type Priority = 'critical' | 'high' | 'medium' | 'low'

export type Complexity = 'simple' | 'medium' | 'complex' | 'epic'

export type ApprovalStatus = 'pending' | 'approved' | 'rejected' | 'expired'

export type ApprovalRiskLevel = 'low' | 'medium' | 'high' | 'critical'

export type SeniorityLevel =
  | 'junior'
  | 'mid'
  | 'senior'
  | 'lead'
  | 'principal'
  | 'director'
  | 'vp'
  | 'c_suite'

export type AgentStatus = 'active' | 'onboarding' | 'on_leave' | 'terminated'

export type AutonomyLevel = 'full' | 'semi' | 'supervised' | 'locked'

export type HumanRole =
  | 'ceo'
  | 'manager'
  | 'board_member'
  | 'pair_programmer'
  | 'observer'

export type DepartmentName =
  | 'executive'
  | 'product'
  | 'design'
  | 'engineering'
  | 'quality_assurance'
  | 'data_analytics'
  | 'operations'
  | 'creative_marketing'
  | 'security'

export type ProjectStatus =
  | 'planning'
  | 'active'
  | 'on_hold'
  | 'completed'
  | 'cancelled'

// ── Response Envelopes ───────────────────────────────────────

export interface ApiResponse<T> {
  data: T | null
  error: string | null
  success: boolean
}

export interface PaginationMeta {
  total: number
  offset: number
  limit: number
}

export interface PaginatedResponse<T> {
  data: T[]
  error: string | null
  success: boolean
  pagination: PaginationMeta
}

// ── Auth ─────────────────────────────────────────────────────

export interface SetupRequest {
  username: string
  password: string
}

export interface LoginRequest {
  username: string
  password: string
}

export interface ChangePasswordRequest {
  current_password: string
  new_password: string
}

export interface TokenResponse {
  token: string
  expires_in: number
  must_change_password: boolean
}

export interface UserInfoResponse {
  id: string
  username: string
  role: HumanRole
  must_change_password: boolean
}

// ── Tasks ────────────────────────────────────────────────────

export interface Task {
  id: string
  title: string
  description: string
  type: TaskType
  status: TaskStatus
  priority: Priority
  project: string
  created_by: string
  assigned_to: string | null
  estimated_complexity: Complexity
  budget_limit: number
  cost_usd: number
  version: number
  created_at: string
  updated_at: string
}

export interface CreateTaskRequest {
  title: string
  description: string
  type: TaskType
  priority?: Priority
  project: string
  created_by: string
  assigned_to?: string | null
  estimated_complexity?: Complexity
  budget_limit?: number
}

export interface UpdateTaskRequest {
  title?: string
  description?: string
  priority?: Priority
  assigned_to?: string | null
  budget_limit?: number
  expected_version?: number
}

export interface TransitionTaskRequest {
  target_status: TaskStatus
  assigned_to?: string | null
  expected_version?: number
}

export interface CancelTaskRequest {
  reason: string
}

export interface TaskFilters {
  status?: TaskStatus
  assigned_to?: string
  project?: string
  offset?: number
  limit?: number
}

// ── Approvals ────────────────────────────────────────────────

export interface ApprovalItem {
  id: string
  action_type: string
  title: string
  description: string
  requested_by: string
  risk_level: ApprovalRiskLevel
  status: ApprovalStatus
  ttl_seconds: number | null
  task_id: string | null
  metadata: Record<string, string>
  decided_by: string | null
  decision_comment: string | null
  created_at: string
  decided_at: string | null
  expires_at: string | null
}

export interface CreateApprovalRequest {
  action_type: string
  title: string
  description: string
  requested_by: string
  risk_level: ApprovalRiskLevel
  ttl_seconds?: number
  task_id?: string
  metadata?: Record<string, string>
}

export interface ApproveRequest {
  comment?: string
}

export interface RejectRequest {
  reason: string
}

export interface ApprovalFilters {
  status?: ApprovalStatus
  risk_level?: ApprovalRiskLevel
  action_type?: string
  offset?: number
  limit?: number
}

// ── Agents ───────────────────────────────────────────────────

export interface PersonalityConfig {
  risk_tolerance: string
  creativity_level: string
  decision_making_style: string
  collaboration_preference: string
  conflict_approach: string
}

export interface AgentConfig {
  name: string
  role: string
  seniority: SeniorityLevel
  department: DepartmentName
  team: string | null
  status: AgentStatus
  model: string
  personality: PersonalityConfig
  tools: string[]
  description: string
}

// ── Budget ───────────────────────────────────────────────────

export interface CostRecord {
  id: string
  agent_id: string
  task_id: string | null
  model: string
  input_tokens: number
  output_tokens: number
  cost_usd: number
  timestamp: string
}

export interface BudgetConfig {
  daily_limit_usd: number
  monthly_limit_usd: number
  per_task_limit_usd: number
  per_agent_limit_usd: number
  alert_threshold_percent: number
}

export interface AgentSpending {
  agent_id: string
  total_cost_usd: number
}

// ── Analytics ────────────────────────────────────────────────

export interface OverviewMetrics {
  total_tasks: number
  tasks_by_status: Record<string, number>
  total_agents: number
  total_cost_usd: number
}

// ── Company / Organization ───────────────────────────────────

export interface Department {
  name: DepartmentName
  display_name: string
  teams: TeamConfig[]
}

export interface TeamConfig {
  name: string
  members: string[]
}

export interface CompanyConfig {
  company_name: string
  agents: AgentConfig[]
  departments: Department[]
}

// ── Providers ────────────────────────────────────────────────

export interface ProviderModelConfig {
  name: string
  aliases: string[]
  input_cost_per_1k: number
  output_cost_per_1k: number
}

export interface ProviderConfig {
  name: string
  driver: string
  models: ProviderModelConfig[]
  enabled: boolean
}

// ── Messages ─────────────────────────────────────────────────

export interface Message {
  id: string
  channel: string
  sender: string
  content: string
  timestamp: string
  metadata: Record<string, string>
}

export interface Channel {
  name: string
  description: string
}

// ── Health ───────────────────────────────────────────────────

export interface HealthStatus {
  status: 'ok' | 'degraded' | 'down'
  persistence: boolean
  message_bus: boolean
  version: string
  uptime_seconds: number
}

// ── Autonomy ─────────────────────────────────────────────────

export interface AutonomyLevelResponse {
  agent_id: string
  level: AutonomyLevel
  promotion_pending: boolean
}

export interface AutonomyLevelRequest {
  level: AutonomyLevel
}

// ── WebSocket ────────────────────────────────────────────────

export type WsChannel =
  | 'tasks'
  | 'agents'
  | 'budget'
  | 'messages'
  | 'system'
  | 'approvals'

export type WsEventType =
  | 'task.created'
  | 'task.updated'
  | 'task.status_changed'
  | 'task.assigned'
  | 'agent.hired'
  | 'agent.fired'
  | 'agent.status_changed'
  | 'budget.record_added'
  | 'budget.alert'
  | 'message.sent'
  | 'system.error'
  | 'system.startup'
  | 'system.shutdown'
  | 'approval.submitted'
  | 'approval.approved'
  | 'approval.rejected'
  | 'approval.expired'

export interface WsEvent {
  event_type: WsEventType
  channel: WsChannel
  timestamp: string
  payload: Record<string, unknown>
}

export interface WsSubscribeMessage {
  action: 'subscribe'
  channels: WsChannel[]
  filters?: Record<string, string>
}

export interface WsUnsubscribeMessage {
  action: 'unsubscribe'
  channels: WsChannel[]
}

export interface WsAckMessage {
  action: 'subscribed' | 'unsubscribed'
  channels: WsChannel[]
}

export interface WsErrorMessage {
  error: string
}

// ── Pagination helpers ───────────────────────────────────────

export interface PaginationParams {
  offset?: number
  limit?: number
}
