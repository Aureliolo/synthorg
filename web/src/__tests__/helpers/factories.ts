import type {
  AgentActivityEvent,
  AgentPerformanceSummary,
  DashboardAgentConfig,
} from '@/api/types/agents'
import type { DepartmentHealth } from '@/api/types/analytics'
import type { ApprovalResponse } from '@/api/types/approvals'
import type { Artifact } from '@/api/types/artifacts'
import type { Channel, Message } from '@/api/types/messages'
import type { CompanyConfig, DashboardDepartment } from '@/api/types/org'
import type { Plan, PlanItem } from '@/api/types/plans'
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
    assigned_to_name: 'Engineer',
    dependency_titles: {},
    requested_by_user_id: null,
    reviewers: [],
    dependencies: [],
    artifacts_expected: [],
    acceptance_criteria: [],
    estimated_complexity: 'medium',
    stakes: 'normal',
    budget_limit: 10,
    deadline: null,
    max_retries: 3,
    parent_task_id: null,
    delegation_chain: [],
    task_structure: null,
    coordination_topology: 'auto',
    middleware_override: null,
    source: null,
    blocked_reason: null,
    metadata: {},
    hard_ceiling: null,
    hard_token_ceiling: null,
    forecast_id: null,
    plan_id: null,
    plan_item_id: null,
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
    status: 'active',
    model: {
      provider: 'test-provider',
      model_id: 'test-capable-001',
      temperature: 0.7,
      // Null is what the API answers for an agent nobody pinned a ceiling on,
      // which is every agent by default: the backend reads the ceiling from
      // `engine.agent_max_response_tokens` unless one is set here. Fixtures
      // that all stated a number left that shape untested everywhere.
      max_tokens: null,
    },
    memory: { type: 'persistent' },
    tools: { access_level: 'standard', allowed: ['code_edit'], denied: [] },
    authority: {},
    autonomy_level: 'semi',
    strategic_output_mode: null,
    capability: null,
    model_requirement: null,
    model_capabilities: null,
    model_capability_status: 'unresolved',
    hiring_date: '2026-03-01T00:00:00Z',
    ...overrides,
  }
}

/** Intentionally accepts `string` for test flexibility (non-enum dept names). */
export function makeDepartment(name: string, overrides?: Partial<DashboardDepartment>): DashboardDepartment {
  return {
    name: name,
    display_name: name.charAt(0).toUpperCase() + name.slice(1),
    autonomy_level: null,
    budget_percent: 0,
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
      makeAgent('alice', { department: 'engineering', role: 'Lead Developer' }),
      makeAgent('bob', { department: 'engineering', role: 'Developer' }),
      makeAgent('carol', { department: 'product', role: 'Product Manager' }),
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
    department_name: name,
    agent_count: 3,
    active_agent_count: 2,
    currency: 'EUR',
    avg_performance_score: 7.5,
    department_cost_7d: 12.5,
    cost_trend: [],
    total_runs: 12,
    task_success_rate: 0.83,
    utilization_percent: 85,
    utilization_degraded: false,
    health_score: 83,
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
    consumed_at: null,
    evidence_package: null,
    seconds_remaining: null,
    urgency_level: 'no_expiry',
    task: null,
    project: null,
    agent: null,
    run: null,
    ...overrides,
  }
}

export function makeActivityEvent(overrides?: Partial<AgentActivityEvent>): AgentActivityEvent {
  return {
    event_type: 'task_completed',
    timestamp: '2026-03-25T12:00:00Z',
    description: 'Task succeeded',
    related_ids: {},
    actor_name: null,
    subject_title: null,
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
    created_by_name: 'Ada Engineer',
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
    lead: 'agent-eng',
    lead_name: 'Engineer',
    plan_id: null,
    deadline: '2026-06-01T00:00:00Z',
    budget: 500,
    status: 'active',
    autonomy_mode: null,
    version: 1,
    created_at: '2026-03-30T12:00:00Z',
    updated_at: '2026-03-30T12:00:00Z',
    ...overrides,
  }
}

export function makePlanItem(id: string, overrides?: Partial<PlanItem>): PlanItem {
  return {
    id,
    title: `Item ${id}`,
    description: `Description for ${id}`,
    parent_id: null,
    unsplit_reason: null,
    dependencies: [],
    owner: null,
    owner_name: null,
    acceptance_criteria: [`${id} is done`],
    // Non-empty: the backend rejects a work item declaring no deliverable, so
    // an empty default would model a plan the API cannot return.
    expected_artifacts: [`src/${id}.ts`],
    required_skills: [],
    required_tags: [],
    estimated_complexity: 'medium',
    stakes: 'normal',
    kind: 'work',
    options: [],
    chosen_option_id: null,
    satisfies: [],
    ...overrides,
  }
}

export function makePlan(id: string, overrides?: Partial<Plan>): Plan {
  return {
    id,
    project: 'beachhead',
    project_name: 'Beachhead',
    objective_id: `objective-${id}`,
    objective_title: `Ship ${id}`,
    parent_task_id: 'task-root',
    items: [makePlanItem('item-1')],
    task_structure: 'sequential',
    coordination_topology: 'auto',
    status: 'pending_review',
    failure_reason: null,
    forecast_id: null,
    review: null,
    // Null is the ordinary case for both: the configured planner produced the
    // items, and a real panel reviewed them, so neither has anything to say.
    planning_strategy: null,
    review_absent_reason: null,
    decomposition_progress: null,
    pending_decision: null,
    open_questions: [],
    assumptions: [],
    objective_criteria: [],
    version_history: [],
    replan_generation: 0,
    version: 1,
    created_at: '2026-07-01T10:00:00Z',
    updated_at: '2026-07-01T10:00:00Z',
    ...overrides,
  }
}
