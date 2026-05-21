import type {
  AgentActivityEvent,
  AgentPerformanceSummary,
  CareerEvent,
  DashboardAgentConfig,
} from '@/api/types/agents'
import type { DepartmentHealth } from '@/api/types/analytics'
import type { ApprovalResponse } from '@/api/types/approvals'
import type { Artifact } from '@/api/types/artifacts'
import type { MeetingResponse } from '@/api/types/meetings'
import type { Channel, Message } from '@/api/types/messages'
import type { CompanyConfig, DashboardDepartment } from '@/api/types/org'
import type { Project } from '@/api/types/projects'
import type { DashboardTask } from '@/api/types/tasks'

export function makeTask(id: string, overrides?: Partial<DashboardTask>): DashboardTask
export function makeTask(id: string, title: string, overrides?: Partial<DashboardTask>): DashboardTask
export function makeTask(id: string, titleOrOverrides?: string | Partial<DashboardTask>, overrides?: Partial<DashboardTask>): DashboardTask {
  const title = typeof titleOrOverrides === 'string' ? titleOrOverrides : `Task ${id}`
  const finalOverrides = typeof titleOrOverrides === 'object' ? titleOrOverrides : overrides
  return {
    id,
    title,
    description: 'Description',
    type: 'development',
    status: 'assigned',
    priority: 'medium',
    project: 'test-project',
    created_by: 'agent-cto',
    assigned_to: 'agent-eng',
    reviewers: [],
    dependencies: [],
    artifacts_expected: [],
    acceptance_criteria: [],
    estimated_complexity: 'medium',
    budget_limit: 10,
    deadline: null,
    max_retries: 3,
    parent_task_id: null,
    delegation_chain: [],
    task_structure: null,
    coordination_topology: 'auto',
    middleware_override: null,
    source: null,
    metadata: {},
    hard_ceiling: null,
    forecast_id: null,
    version: 1,
    created_at: '2026-03-20T10:00:00Z',
    updated_at: '2026-03-25T14:00:00Z',
    ...finalOverrides,
  }
}

export function makeAgent(name: string, overrides?: Partial<DashboardAgentConfig>): DashboardAgentConfig {
  return {
    id: `agent-${name}`,
    name,
    role: 'Developer',
    department: 'engineering',
    level: 'mid',
    status: 'active',
    personality: {
      traits: ['analytical'],
      communication_style: 'direct',
      risk_tolerance: 'medium',
      creativity: 'medium',
      description: 'A test agent',
    },
    model: {
      provider: 'test-provider',
      model_id: 'test-medium-001',
      temperature: 0.7,
      max_tokens: 4096,
    },
    memory: { type: 'persistent' },
    tools: { access_level: 'standard', allowed: ['code_edit'], denied: [] },
    authority: {},
    autonomy_level: 'semi',
    strategic_output_mode: null,
    personality_preset: null,
    tier: null,
    model_requirement: null,
    hiring_date: '2026-03-01T00:00:00Z',
    ...overrides,
  }
}

/** Intentionally accepts `string` for test flexibility (non-enum dept names). */
export function makeDepartment(name: string, overrides?: Partial<DashboardDepartment>): DashboardDepartment {
  return {
    name: name as DashboardDepartment['name'],
    display_name: name.charAt(0).toUpperCase() + name.slice(1),
    autonomy_level: null,
    budget_percent: 0,
    ceremony_policy: null,
    head: null,
    head_id: null,
    policies: {
      approval_chains: [],
      review_requirements: {
        min_reviewers: 0,
        required_reviewer_roles: [],
        self_review_allowed: true,
      },
    },
    reporting_lines: [],
    teams: [],
    ...overrides,
  }
}

export function makeCompanyConfig(overrides?: Partial<CompanyConfig>): CompanyConfig {
  return {
    company_name: 'Test Corp',
    agents: [
      makeAgent('alice', { department: 'engineering', role: 'Lead Developer', level: 'lead' }),
      makeAgent('bob', { department: 'engineering', role: 'Developer' }),
      makeAgent('carol', { department: 'product', role: 'Product Manager', level: 'senior' }),
    ],
    departments: [
      makeDepartment('engineering'),
      makeDepartment('product'),
    ],
    ...overrides,
  }
}

export function makeDepartmentHealth(name: string, overrides?: Partial<DepartmentHealth>): DepartmentHealth {
  return {
    department_name: name as DepartmentHealth['department_name'],
    agent_count: 3,
    active_agent_count: 2,
    currency: 'EUR',
    avg_performance_score: 7.5,
    department_cost_7d: 12.5,
    cost_trend: [],
    collaboration_score: 6.0,
    utilization_percent: 85,
    ...overrides,
  }
}

export function makeMeeting(id: string, overrides?: Partial<MeetingResponse>): MeetingResponse {
  return {
    meeting_id: id,
    meeting_type_name: 'daily_standup',
    protocol_type: 'round_robin',
    status: 'completed',
    minutes: {
      meeting_id: id,
      protocol_type: 'round_robin',
      leader_id: 'agent-alice',
      participant_ids: ['agent-alice', 'agent-bob'],
      agenda: {
        title: 'Daily Standup',
        context: 'Regular sync',
        items: [{ title: 'Status updates', description: 'Share progress', presenter_id: null }],
      },
      contributions: [
        {
          agent_id: 'agent-alice',
          content: 'Completed the API endpoint work.',
          phase: 'round_robin_turn',
          turn_number: 1,
          input_tokens: 200,
          output_tokens: 150,
          timestamp: '2026-03-25T10:01:00Z',
        },
        {
          agent_id: 'agent-bob',
          content: 'Working on test coverage.',
          phase: 'round_robin_turn',
          turn_number: 2,
          input_tokens: 180,
          output_tokens: 120,
          timestamp: '2026-03-25T10:02:00Z',
        },
      ],
      summary: 'Team is on track.',
      decisions: ['Continue current sprint tasks'],
      action_items: [{ description: 'Finish test coverage', assignee_id: 'agent-bob', priority: 'medium' }],
      conflicts_detected: false,
      total_input_tokens: 380,
      total_output_tokens: 270,
      total_tokens: 650,
      started_at: '2026-03-25T10:00:00Z',
      ended_at: '2026-03-25T10:05:00Z',
    },
    error_message: null,
    token_budget: 2000,
    token_usage_by_participant: { 'agent-alice': 350, 'agent-bob': 300 },
    contribution_rank: ['agent-alice', 'agent-bob'],
    meeting_duration_seconds: 300,
    ...overrides,
  }
}

export function makeMessage(id: string, overrides?: Partial<Message>): Message {
  return {
    id,
    timestamp: '2026-03-28T09:00:00.000Z',
    sender: 'agent-eng',
    to: '#engineering',
    type: 'task_update',
    priority: 'normal',
    channel: '#engineering',
    text: `Message ${id} content`,
    parts: [{ type: 'text', text: `Message ${id} content` }],
    attachments: [],
    metadata: {
      task_id: null,
      project_id: null,
      tokens_used: null,
      cost: null,
      extra: [],
    },
    ...overrides,
  }
}

export function makeChannel(name: string, overrides?: Partial<Channel>): Channel {
  return {
    name,
    type: 'topic',
    subscribers: ['agent-eng', 'agent-cto'],
    ...overrides,
  }
}

export function makeApproval(id: string, overrides?: Partial<ApprovalResponse>): ApprovalResponse {
  return {
    id,
    action_type: 'code:create',
    title: `Approval ${id}`,
    description: 'Test approval description',
    requested_by: 'agent-eng',
    risk_level: 'medium',
    source: 'review_gate',
    status: 'pending',
    task_id: null,
    metadata: {},
    decided_by: null,
    decision_reason: null,
    created_at: new Date(Date.now() - 3600_000).toISOString(), // 1 hour ago
    decided_at: null,
    expires_at: null,
    evidence_package: null,
    seconds_remaining: null,
    urgency_level: 'no_expiry',
    ...overrides,
  }
}

export function makeActivityEvent(overrides?: Partial<AgentActivityEvent>): AgentActivityEvent {
  return {
    event_type: 'task_completed',
    timestamp: '2026-03-25T12:00:00Z',
    description: 'Completed task task-1',
    related_ids: {},
    ...overrides,
  }
}

export function makeCareerEvent(overrides?: Partial<CareerEvent>): CareerEvent {
  return {
    event_type: 'hired',
    timestamp: '2026-03-01T00:00:00Z',
    description: 'Hired as Developer',
    initiated_by: 'system',
    metadata: {},
    ...overrides,
  }
}

export function makePerformanceSummary(
  agentName: string,
  overrides?: Partial<AgentPerformanceSummary>,
): AgentPerformanceSummary {
  return {
    agent_name: agentName,
    tasks_completed_total: 10,
    tasks_completed_7d: 3,
    tasks_completed_30d: 8,
    avg_completion_time_seconds: 3600,
    success_rate_percent: 90,
    cost_per_task: 0.5,
    quality_score: 8.5,
    collaboration_score: 7.0,
    trend_direction: 'stable',
    windows: [],
    trends: [],
    ...overrides,
  }
}

export function makeArtifact(id: string, overrides?: Partial<Artifact>): Artifact {
  return {
    id,
    type: 'code',
    path: `src/output/${id}.py`,
    task_id: 'task-001',
    created_by: 'agent-eng',
    description: `Artifact ${id}`,
    project_id: null,
    content_type: 'text/plain',
    size_bytes: 1024,
    created_at: '2026-03-30T12:00:00Z',
    ...overrides,
  }
}

export function makeProject(id: string, overrides?: Partial<Project>): Project {
  return {
    id,
    name: `Project ${id}`,
    description: `Description for ${id}`,
    team: ['agent-eng', 'agent-qa'],
    lead: 'agent-eng',
    task_ids: ['task-001', 'task-002'],
    deadline: '2026-06-01T00:00:00Z',
    budget: 500,
    status: 'active',
    ...overrides,
  }
}
