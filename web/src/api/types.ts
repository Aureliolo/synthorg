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

export type RiskTolerance = 'low' | 'medium' | 'high'

export type CreativityLevel = 'low' | 'medium' | 'high'

export type DecisionMakingStyle = 'analytical' | 'intuitive' | 'consultative' | 'directive'

export type CollaborationPreference = 'independent' | 'pair' | 'team'

export type CommunicationVerbosity = 'terse' | 'balanced' | 'verbose'

export type ConflictApproach = 'avoid' | 'accommodate' | 'compete' | 'compromise' | 'collaborate'

export type TaskStructure = 'sequential' | 'parallel' | 'mixed'

export type CoordinationTopology = 'sas' | 'centralized' | 'decentralized' | 'context_dependent' | 'auto'

export type ToolAccessLevel = 'sandboxed' | 'restricted' | 'standard' | 'elevated' | 'custom'

export type MemoryLevel = 'persistent' | 'project' | 'session' | 'none'

// ── Response Envelopes ───────────────────────────────────────

/** Discriminated API response envelope. */
export type ApiResponse<T> =
  | { data: T; error: null; success: true }
  | { data: null; error: string; success: false }

export interface PaginationMeta {
  total: number
  offset: number
  limit: number
}

/** Discriminated paginated response envelope. */
export type PaginatedResponse<T> =
  | { data: T[]; error: null; success: true; pagination: PaginationMeta }
  | { data: null; error: string; success: false; pagination: null }

// ── Auth ─────────────────────────────────────────────────────

export interface CredentialsRequest {
  username: string
  password: string
}

/** Alias for setup endpoint. */
export type SetupRequest = CredentialsRequest

/** Alias for login endpoint. */
export type LoginRequest = CredentialsRequest

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

export interface AcceptanceCriterion {
  description: string
  met: boolean
}

export interface ExpectedArtifact {
  name: string
  type: string
}

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
  reviewers: string[]
  dependencies: string[]
  artifacts_expected: ExpectedArtifact[]
  acceptance_criteria: AcceptanceCriterion[]
  estimated_complexity: Complexity
  budget_limit: number
  cost_usd?: number
  deadline: string | null
  max_retries: number
  parent_task_id: string | null
  delegation_chain: string[]
  task_structure: TaskStructure | null
  coordination_topology: CoordinationTopology
  version?: number
  created_at?: string
  updated_at?: string
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
  task_id: string | null
  metadata: Record<string, string>
  decided_by: string | null
  decision_reason: string | null
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
  traits: string[]
  communication_style: string
  risk_tolerance: RiskTolerance
  creativity: CreativityLevel
  description: string
  openness: number
  conscientiousness: number
  extraversion: number
  agreeableness: number
  stress_response: number
  decision_making: DecisionMakingStyle
  collaboration: CollaborationPreference
  verbosity: CommunicationVerbosity
  conflict_approach: ConflictApproach
}

export interface ModelConfig {
  provider: string
  model_id: string
  temperature: number
  max_tokens: number
  fallback_model: string | null
}

export interface SkillSet {
  primary: string[]
  secondary: string[]
}

export interface MemoryConfig {
  type: MemoryLevel
  retention_days: number | null
}

export interface ToolPermissions {
  access_level: ToolAccessLevel
  allowed: string[]
  denied: string[]
}

/**
 * Agent identity as returned by the API.
 * Mirrors backend AgentIdentity with serialization adaptations
 * (UUIDs as strings, dates as ISO strings, authority omitted from listing response).
 */
export interface AgentConfig {
  id: string
  name: string
  role: string
  department: string
  level: SeniorityLevel
  status: AgentStatus
  personality: PersonalityConfig
  model: ModelConfig
  skills: SkillSet
  memory: MemoryConfig
  tools: ToolPermissions
  autonomy_level: AutonomyLevel | null
  hiring_date: string
}

// ── Budget ───────────────────────────────────────────────────

export interface CostRecord {
  agent_id: string
  task_id: string
  provider: string
  model: string
  input_tokens: number
  output_tokens: number
  cost_usd: number
  timestamp: string
  call_category: 'productive' | 'coordination' | 'system' | null
}

export interface BudgetAlertConfig {
  warn_at: number
  critical_at: number
  hard_stop_at: number
}

export interface AutoDowngradeConfig {
  enabled: boolean
  threshold: number
  downgrade_map: [string, string][]
  boundary: 'task_assignment'
}

export interface BudgetConfig {
  total_monthly: number
  alerts: BudgetAlertConfig
  per_task_limit: number
  per_agent_daily_limit: number
  auto_downgrade: AutoDowngradeConfig
  reset_day: number
}

export interface AgentSpending {
  agent_id: string
  total_cost_usd: number
}

// ── Analytics ────────────────────────────────────────────────

export interface OverviewMetrics {
  total_tasks: number
  tasks_by_status: Record<TaskStatus, number>
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
  id: string
  alias: string | null
  cost_per_1k_input: number
  cost_per_1k_output: number
  max_context: number
  estimated_latency_ms: number | null
}

/**
 * Provider configuration as returned by the listing endpoint.
 * The backend MUST NOT serialize `api_key` to the frontend.
 * If it does, the provider store strips it before storing.
 */
export interface ProviderConfig {
  driver: string
  base_url: string | null
  models: ProviderModelConfig[]
}

// ── Messages ─────────────────────────────────────────────────

export type MessageType =
  | 'task_update'
  | 'question'
  | 'announcement'
  | 'status_report'
  | 'escalation'
  | 'delegation'

export type MessagePriority = 'low' | 'normal' | 'high' | 'urgent'

export type AttachmentType = 'artifact' | 'file' | 'link'

export interface Attachment {
  type: AttachmentType
  ref: string
}

export interface MessageMetadata {
  task_id: string | null
  project_id: string | null
  tokens_used: number | null
  cost_usd: number | null
  extra: [string, string][]
}

export interface Message {
  id: string
  timestamp: string
  sender: string
  to: string
  type: MessageType
  priority: MessagePriority
  channel: string
  content: string
  attachments: Attachment[]
  metadata: MessageMetadata
}

export type ChannelType = 'topic' | 'direct' | 'broadcast'

export interface Channel {
  name: string
  type: ChannelType
  subscribers: string[]
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

// ── Event handler type ──────────────────────────────────────

export type WsEventHandler = (event: WsEvent) => void

// ── Pagination helpers ───────────────────────────────────────

export interface PaginationParams {
  offset?: number
  limit?: number
}
