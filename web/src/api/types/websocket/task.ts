/** Task, agent, company, and department WebSocket payload interfaces. */

export interface WsTaskCreatedPayload {
  task_id: string
  title: string
  status: string
  assigned_agent_id?: string | null
  project_id?: string | null
}

export interface WsTaskUpdatedPayload {
  task_id: string
  title?: string | null
  status?: string | null
  assigned_agent_id?: string | null
}

export interface WsTaskStatusChangedPayload {
  task_id: string
  from_status?: string | null
  to_status: string
}

export interface WsTaskAssignedPayload {
  task_id: string
  agent_id: string
}

export interface WsAgentCreatedPayload {
  name: string
  role: string
  department: string
}

export interface WsAgentUpdatedPayload {
  name: string
  department: string
}

export interface WsAgentDeletedPayload {
  name: string
}

export interface WsAgentHiredPayload {
  agent_id: string
  name: string
  role: string
  department: string
}

export interface WsAgentFiredPayload {
  agent_id: string
  name: string
  reason?: string | null
}

export interface WsAgentStatusChangedPayload {
  agent_id: string
  from_status?: string | null
  to_status: string
}

export interface WsAgentsReorderedPayload {
  department?: string | null
  readonly agent_names: readonly string[]
}

export interface WsCompanyUpdatedPayload {
  company_name?: string | null
  autonomy_level?: string | null
  budget_monthly?: number | null
  communication_pattern?: string | null
}

export interface WsDepartmentCreatedPayload {
  name: string
  description?: string | null
  budget_percent?: number | null
}

export interface WsDepartmentUpdatedPayload {
  name: string
  description?: string | null
}

export interface WsDepartmentDeletedPayload {
  name: string
}

export interface WsDepartmentsReorderedPayload {
  readonly department_names: readonly string[]
}

export interface WsPersonalityTrimmedPayload {
  agent_id: string
  agent_name: string
  task_id: string
  trim_tier: 1 | 2 | 3
  before_tokens: number
  after_tokens: number
  max_tokens: number
  budget_met: boolean
}
