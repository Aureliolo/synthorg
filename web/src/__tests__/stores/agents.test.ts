import { http, HttpResponse } from 'msw'
import { useAgentsStore } from '@/stores/agents'
import { useToastStore } from '@/stores/toast'
import { apiError, apiSuccess } from '@/mocks/handlers'
import { server } from '@/test-setup'
import type { AgentConfig, AgentPerformanceSummary } from '@/api/types/agents'
import type { Task } from '@/api/types/tasks'

// Bidi-override chars via fromCharCode so ESLint's
// ``security/detect-bidi-characters`` rule sees only hex in source.
const RLO = String.fromCharCode(0x202e)
const LRO = String.fromCharCode(0x202d)

function makeAgent(overrides: Partial<AgentConfig> = {}): AgentConfig {
  return {
    id: 'agent-001',
    name: 'Alice Smith',
    role: 'Software Engineer',
    department: 'engineering',
    level: 'senior',
    status: 'active',
    personality: {
      traits: ['analytical'],
      communication_style: 'direct',
      risk_tolerance: 'medium',
      creativity: 'high',
      description: 'test',
      openness: 0.8,
      conscientiousness: 0.7,
      extraversion: 0.5,
      agreeableness: 0.6,
      stress_response: 0.9,
      decision_making: 'analytical',
      collaboration: 'team',
      verbosity: 'balanced',
      conflict_approach: 'collaborate',
    },
    model: {
      provider: 'test-provider',
      model_id: 'test-large-001',
      temperature: 0.7,
      max_tokens: 4096,
      fallback_model: null,
    },
    memory: { type: 'persistent', retention_days: null },
    tools: { access_level: 'standard', allowed: ['git'], denied: [] },
    authority: {},
    autonomy_level: 'semi',
    hiring_date: '2026-01-15T00:00:00Z',
    ...overrides,
  }
}

function makePerformance(): AgentPerformanceSummary {
  return {
    agent_name: 'Alice Smith',
    tasks_completed_total: 50,
    tasks_completed_7d: 5,
    tasks_completed_30d: 20,
    avg_completion_time_seconds: 1800,
    success_rate_percent: 90,
    cost_per_task: 0.25,
    quality_score: 7.5,
    collaboration_score: 8.0,
    trend_direction: 'stable',
    windows: [],
    trends: [],
  }
}

function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    id: 'task-001',
    title: 'Test Task',
    description: 'A test task',
    type: 'development',
    status: 'completed',
    priority: 'medium',
    project: 'test-project',
    created_by: 'system',
    assigned_to: 'Alice Smith',
    reviewers: [],
    dependencies: [],
    artifacts_expected: [],
    acceptance_criteria: [],
    estimated_complexity: 'medium',
    budget_limit: 1.0,
    deadline: null,
    max_retries: 3,
    parent_task_id: null,
    delegation_chain: [],
    task_structure: null,
    coordination_topology: 'centralized',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: undefined,
    ...overrides,
  }
}

type HandlerResult = Response | Promise<Response>
type Fixture = Partial<{
  agentList: () => HandlerResult
  agent: (name: string) => HandlerResult
  performance: () => HandlerResult
  activity: () => HandlerResult
  history: () => HandlerResult
  tasks: () => HandlerResult
}>

function installAgentHandlers(f: Fixture = {}) {
  server.use(
    http.get('/api/v1/agents', () =>
      f.agentList
        ? f.agentList()
        : HttpResponse.json({
            data: [makeAgent()],
            error: null,
            error_detail: null,
            success: true,
            pagination: { total: 1, offset: 0, limit: 200 },
          }),
    ),
    http.get('/api/v1/agents/:name', ({ params }) =>
      f.agent
        ? f.agent(String(params.name))
        : HttpResponse.json(apiSuccess(makeAgent({ name: String(params.name) }))),
    ),
    http.get('/api/v1/agents/:name/performance', () =>
      f.performance
        ? f.performance()
        : HttpResponse.json(apiSuccess(makePerformance())),
    ),
    http.get('/api/v1/agents/:name/activity', () =>
      f.activity
        ? f.activity()
        : HttpResponse.json({
            data: [],
            error: null,
            error_detail: null,
            success: true,
            pagination: { total: 0, offset: 0, limit: 50 },
          }),
    ),
    http.get('/api/v1/agents/:name/history', () =>
      f.history ? f.history() : HttpResponse.json(apiSuccess([])),
    ),
    http.get('/api/v1/tasks', () =>
      f.tasks
        ? f.tasks()
        : HttpResponse.json({
            data: [],
            error: null,
            error_detail: null,
            success: true,
            pagination: { total: 0, offset: 0, limit: 50 },
          }),
    ),
  )
}

beforeEach(() => {
  useToastStore.getState().dismissAll()
  useAgentsStore.setState({
    agents: [],
    totalAgents: 0,
    listLoading: false,
    listError: null,
    searchQuery: '',
    departmentFilter: null,
    levelFilter: null,
    statusFilter: null,
    sortBy: 'name',
    sortDirection: 'asc',
    selectedAgent: null,
    performance: null,
    agentTasks: [],
    activity: [],
    activityTotal: 0,
    activityLoading: false,
    careerHistory: [],
    detailLoading: false,
    detailError: null,
    runtimeStatuses: {},
  })
})

describe('fetchAgents', () => {
  it('sets agents on success', async () => {
    const agents = [makeAgent(), makeAgent({ name: 'Bob Jones' })]
    installAgentHandlers({
      agentList: () =>
        HttpResponse.json({
          data: agents,
          error: null,
          error_detail: null,
          success: true,
          pagination: { total: 2, offset: 0, limit: 200 },
        }),
    })

    await useAgentsStore.getState().fetchAgents()

    const state = useAgentsStore.getState()
    expect(state.agents).toHaveLength(2)
    expect(state.totalAgents).toBe(2)
    expect(state.listLoading).toBe(false)
    expect(state.listError).toBeNull()
  })

  it('sets error on failure', async () => {
    installAgentHandlers({
      agentList: () => HttpResponse.json(apiError('Network error')),
    })

    await useAgentsStore.getState().fetchAgents()

    const state = useAgentsStore.getState()
    expect(state.agents).toHaveLength(0)
    expect(state.listLoading).toBe(false)
    expect(state.listError).toBe('Network error')
  })

  it('sets loading to true during fetch', async () => {
    let release!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    installAgentHandlers({
      agentList: async () => {
        await gate
        return HttpResponse.json({
          data: [],
          error: null,
          error_detail: null,
          success: true,
          pagination: { total: 0, offset: 0, limit: 200 },
        })
      },
    })

    const promise = useAgentsStore.getState().fetchAgents()
    expect(useAgentsStore.getState().listLoading).toBe(true)

    release()
    await promise
    expect(useAgentsStore.getState().listLoading).toBe(false)
  })
})

describe('fetchAgentDetail', () => {
  it('fetches agent details in parallel', async () => {
    const agent = makeAgent()
    installAgentHandlers({
      agent: () => HttpResponse.json(apiSuccess(agent)),
    })

    await useAgentsStore.getState().fetchAgentDetail('Alice Smith')

    const state = useAgentsStore.getState()
    expect(state.selectedAgent).toEqual(agent)
    expect(state.performance).not.toBeNull()
    expect(state.detailLoading).toBe(false)
    expect(state.detailError).toBeNull()
  })

  it('degrades gracefully when performance fails', async () => {
    installAgentHandlers({
      performance: () => HttpResponse.json(apiError('fail')),
    })

    await useAgentsStore.getState().fetchAgentDetail('Alice Smith')

    const state = useAgentsStore.getState()
    expect(state.selectedAgent).not.toBeNull()
    expect(state.performance).toBeNull()
    expect(state.detailError).toBe(
      'Some data failed to load: performance metrics. Displayed data may be incomplete.',
    )
  })

  it('degrades gracefully when tasks and history fail', async () => {
    installAgentHandlers({
      tasks: () => HttpResponse.json(apiError('tasks fail')),
      history: () => HttpResponse.json(apiError('history fail')),
    })

    await useAgentsStore.getState().fetchAgentDetail('Alice Smith')

    const state = useAgentsStore.getState()
    expect(state.selectedAgent).not.toBeNull()
    expect(state.performance).not.toBeNull()
    expect(state.agentTasks).toHaveLength(0)
    expect(state.careerHistory).toHaveLength(0)
    expect(state.detailError).toBe(
      'Some data failed to load: task history, career history. Displayed data may be incomplete.',
    )
  })

  it('sets error when agent fetch fails', async () => {
    installAgentHandlers({
      agent: () => HttpResponse.json(apiError('Not found')),
      performance: () => HttpResponse.json(apiError('fail')),
      tasks: () => HttpResponse.json(apiError('fail')),
      activity: () => HttpResponse.json(apiError('fail')),
      history: () => HttpResponse.json(apiError('fail')),
    })

    await useAgentsStore.getState().fetchAgentDetail('Unknown')

    const state = useAgentsStore.getState()
    expect(state.selectedAgent).toBeNull()
    expect(state.detailError).toBe('Not found')
  })

  it('rejects stale responses when a newer fetch starts', async () => {
    const agentA = makeAgent({ name: 'Alice Smith' })
    const agentB = makeAgent({ name: 'Bob Jones', role: 'Designer' })

    let releaseA!: () => void
    const gateA = new Promise<void>((resolve) => {
      releaseA = resolve
    })
    installAgentHandlers({
      agent: async (name: string) => {
        if (name === 'Alice Smith') {
          await gateA
          return HttpResponse.json(apiSuccess(agentA))
        }
        return HttpResponse.json(apiSuccess(agentB))
      },
    })

    const promiseA = useAgentsStore.getState().fetchAgentDetail('Alice Smith')
    const promiseB = useAgentsStore.getState().fetchAgentDetail('Bob Jones')

    await promiseB
    expect(useAgentsStore.getState().selectedAgent?.name).toBe('Bob Jones')

    releaseA()
    await promiseA

    expect(useAgentsStore.getState().selectedAgent?.name).toBe('Bob Jones')
  })

  it('rejects in-flight responses after clearDetail', async () => {
    let release!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    installAgentHandlers({
      agent: async () => {
        await gate
        return HttpResponse.json(apiSuccess(makeAgent()))
      },
    })

    const promise = useAgentsStore.getState().fetchAgentDetail('Alice Smith')

    useAgentsStore.getState().clearDetail()
    expect(useAgentsStore.getState().selectedAgent).toBeNull()

    release()
    await promise

    expect(useAgentsStore.getState().selectedAgent).toBeNull()
    expect(useAgentsStore.getState().detailLoading).toBe(false)
  })
})

describe('fetchMoreActivity', () => {
  it('appends new activity events', async () => {
    const existingEvents = [
      {
        event_type: 'task_completed',
        timestamp: '2026-03-26T12:00:00Z',
        description: 'Task done',
        related_ids: {},
      },
    ]
    useAgentsStore.setState({
      activity: existingEvents,
      activityTotal: 1,
      activityNextCursor: 'cursor-page-2',
      activityHasMore: true,
      selectedAgent: makeAgent({ name: 'Alice Smith' }),
    })

    const newEvents = [
      {
        event_type: 'hired',
        timestamp: '2026-03-25T10:00:00Z',
        description: 'Agent hired',
        related_ids: {},
      },
    ]
    server.use(
      http.get('/api/v1/agents/:name/activity', () =>
        HttpResponse.json({
          data: newEvents,
          error: null,
          error_detail: null,
          success: true,
          pagination: {
            total: 5,
            offset: 1,
            limit: 20,
            next_cursor: null,
            has_more: false,
          },
        }),
      ),
    )

    await useAgentsStore.getState().fetchMoreActivity('Alice Smith')

    expect(useAgentsStore.getState().activity).toHaveLength(2)
    expect(useAgentsStore.getState().activityTotal).toBe(2)
    // Terminal page must clear both cursor fields so a subsequent
    // ``fetchMoreActivity`` call short-circuits on the ``!hasMore ||
    // !nextCursor`` guard instead of replaying the last cursor.
    expect(useAgentsStore.getState().activityHasMore).toBe(false)
    expect(useAgentsStore.getState().activityNextCursor).toBeNull()
  })

  it('caps activity at MAX_ACTIVITIES (100)', async () => {
    const existingEvents = Array.from({ length: 99 }, (_, i) => ({
      event_type: 'task_completed',
      timestamp: `2026-03-26T${String(i % 24).padStart(2, '0')}:${String(Math.floor(i / 24)).padStart(2, '0')}:00Z`,
      description: `Event ${i}`,
      related_ids: {},
    }))
    useAgentsStore.setState({
      activity: existingEvents,
      activityTotal: 200,
      activityNextCursor: 'cursor-page-2',
      activityHasMore: true,
      selectedAgent: makeAgent({ name: 'Alice Smith' }),
    })

    const newEvents = Array.from({ length: 5 }, (_, i) => ({
      event_type: 'hired',
      timestamp: `2026-03-25T${String(i % 24).padStart(2, '0')}:00:00Z`,
      description: `New event ${i}`,
      related_ids: {},
    }))
    server.use(
      http.get('/api/v1/agents/:name/activity', () =>
        HttpResponse.json({
          data: newEvents,
          error: null,
          error_detail: null,
          success: true,
          pagination: {
            total: 200,
            offset: 99,
            limit: 20,
            next_cursor: 'cursor-page-3',
            has_more: true,
          },
        }),
      ),
    )

    await useAgentsStore.getState().fetchMoreActivity('Alice Smith')

    expect(useAgentsStore.getState().activity).toHaveLength(100)
    // Intermediate page must advance the cursor + keep ``hasMore``
    // true so the next ``fetchMoreActivity`` call can continue.
    expect(useAgentsStore.getState().activityNextCursor).toBe('cursor-page-3')
    expect(useAgentsStore.getState().activityHasMore).toBe(true)
  })

  it('preserves existing data on failure', async () => {
    const existingEvents = [
      {
        event_type: 'task_completed',
        timestamp: '2026-03-26T12:00:00Z',
        description: 'Task done',
        related_ids: {},
      },
    ]
    useAgentsStore.setState({
      activity: existingEvents,
      activityTotal: 5,
      activityNextCursor: 'cursor-page-2',
      activityHasMore: true,
      selectedAgent: makeAgent({ name: 'Alice Smith' }),
    })

    server.use(
      http.get('/api/v1/agents/:name/activity', () =>
        HttpResponse.json(apiError('Network error')),
      ),
    )

    await useAgentsStore.getState().fetchMoreActivity('Alice Smith')

    expect(useAgentsStore.getState().activity).toHaveLength(1)
    expect(useAgentsStore.getState().activity[0]!.description).toBe('Task done')
    // On failure the previous cursor state is preserved so the user
    // can retry ``fetchMoreActivity`` without losing their place.
    expect(useAgentsStore.getState().activityNextCursor).toBe('cursor-page-2')
    expect(useAgentsStore.getState().activityHasMore).toBe(true)
    // And ``detailError`` surfaces the failure for the UI banner.
    expect(useAgentsStore.getState().detailError).not.toBeNull()
  })
})

describe('filter setters', () => {
  it('sets search query', () => {
    useAgentsStore.getState().setSearchQuery('alice')
    expect(useAgentsStore.getState().searchQuery).toBe('alice')
  })

  it('sets department filter', () => {
    useAgentsStore.getState().setDepartmentFilter('engineering')
    expect(useAgentsStore.getState().departmentFilter).toBe('engineering')
  })

  it('sets level filter', () => {
    useAgentsStore.getState().setLevelFilter('senior')
    expect(useAgentsStore.getState().levelFilter).toBe('senior')
  })

  it('sets status filter', () => {
    useAgentsStore.getState().setStatusFilter('active')
    expect(useAgentsStore.getState().statusFilter).toBe('active')
  })

  it('sets sort by', () => {
    useAgentsStore.getState().setSortBy('department')
    expect(useAgentsStore.getState().sortBy).toBe('department')
  })

  it('sets sort direction', () => {
    useAgentsStore.getState().setSortDirection('desc')
    expect(useAgentsStore.getState().sortDirection).toBe('desc')
  })
})

describe('clearDetail', () => {
  it('resets all detail state', () => {
    useAgentsStore.setState({
      selectedAgent: makeAgent(),
      performance: makePerformance(),
      agentTasks: [makeTask()],
      activity: [
        {
          event_type: 'hired',
          timestamp: '2026-01-01T00:00:00Z',
          description: 'Hired',
          related_ids: {},
        },
      ],
      activityTotal: 10,
      activityLoading: true,
      careerHistory: [
        {
          event_type: 'hired' as const,
          timestamp: '2026-01-01T00:00:00Z',
          description: 'Hired',
          initiated_by: 'system',
          metadata: {},
        },
      ],
      detailLoading: true,
      detailError: 'some error',
    })

    useAgentsStore.getState().clearDetail()

    const state = useAgentsStore.getState()
    expect(state.selectedAgent).toBeNull()
    expect(state.performance).toBeNull()
    expect(state.agentTasks).toHaveLength(0)
    expect(state.activity).toHaveLength(0)
    expect(state.activityTotal).toBe(0)
    expect(state.activityLoading).toBe(false)
    expect(state.careerHistory).toHaveLength(0)
    expect(state.detailLoading).toBe(false)
    expect(state.detailError).toBeNull()
  })
})

describe('runtime statuses (org chart)', () => {
  it('updateRuntimeStatus sets status for an agent', () => {
    useAgentsStore.getState().updateRuntimeStatus('agent-1', 'active')
    expect(useAgentsStore.getState().runtimeStatuses['agent-1']).toBe('active')
  })

  it('updateFromWsEvent updates status on agent.status_changed', () => {
    useAgentsStore.getState().updateFromWsEvent({
      event_type: 'agent.status_changed',
      channel: 'agents',
      timestamp: '2026-03-27T10:00:00Z',
      payload: { agent_id: 'agent-1', status: 'active' },
    })
    expect(useAgentsStore.getState().runtimeStatuses['agent-1']).toBe('active')
  })

  it('updateFromWsEvent ignores events with missing agent_id', () => {
    useAgentsStore.getState().updateFromWsEvent({
      event_type: 'agent.status_changed',
      channel: 'agents',
      timestamp: '2026-03-27T10:00:00Z',
      payload: { status: 'active' },
    })
    expect(Object.keys(useAgentsStore.getState().runtimeStatuses)).toHaveLength(0)
  })

  it('updateFromWsEvent ignores events with missing status', () => {
    useAgentsStore.getState().updateFromWsEvent({
      event_type: 'agent.status_changed',
      channel: 'agents',
      timestamp: '2026-03-27T10:00:00Z',
      payload: { agent_id: 'agent-1' },
    })
    expect(Object.keys(useAgentsStore.getState().runtimeStatuses)).toHaveLength(0)
  })

  it('updateFromWsEvent ignores invalid status values', () => {
    useAgentsStore.getState().updateFromWsEvent({
      event_type: 'agent.status_changed',
      channel: 'agents',
      timestamp: '2026-03-27T10:00:00Z',
      payload: { agent_id: 'agent-1', status: 'invalid_status' },
    })
    expect(Object.keys(useAgentsStore.getState().runtimeStatuses)).toHaveLength(0)
  })

  it('updateFromWsEvent ignores non-status events', () => {
    useAgentsStore.getState().updateFromWsEvent({
      event_type: 'task.created',
      channel: 'tasks',
      timestamp: '2026-03-27T10:00:00Z',
      payload: {},
    })
    expect(Object.keys(useAgentsStore.getState().runtimeStatuses)).toHaveLength(0)
  })

  it('updateFromWsEvent drops frames whose agent_id sanitizes to empty', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    useAgentsStore.getState().updateFromWsEvent({
      event_type: 'agent.status_changed',
      channel: 'agents',
      timestamp: '2026-03-27T10:00:00Z',
      // Bidi-override-only id collapses to '' after sanitization.
      payload: { agent_id: RLO + LRO, status: 'idle' },
    })
    expect(Object.keys(useAgentsStore.getState().runtimeStatuses)).toHaveLength(0)
    warnSpy.mockRestore()
  })

  it('updateFromWsEvent drops frames whose agent_id changes under sanitization', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    // Valid-looking id with an embedded bidi override -- sanitization
    // strips the override, changing the effective id, which could
    // alias a different legitimate agent. Reject the frame.
    useAgentsStore.getState().updateFromWsEvent({
      event_type: 'agent.status_changed',
      channel: 'agents',
      timestamp: '2026-03-27T10:00:00Z',
      payload: { agent_id: `agent-1${RLO}`, status: 'idle' },
    })
    expect(Object.keys(useAgentsStore.getState().runtimeStatuses)).toHaveLength(0)
    warnSpy.mockRestore()
  })
})

describe('personality.trimmed toast dispatch', () => {
  beforeEach(() => {
    useToastStore.getState().dismissAll()
    useAgentsStore.setState({ runtimeStatuses: {} })
  })

  it('does not dispatch a toast for personality.trimmed (handled by notifications pipeline)', () => {
    useAgentsStore.getState().updateFromWsEvent({
      event_type: 'personality.trimmed',
      channel: 'agents',
      timestamp: '2026-03-27T10:00:00Z',
      payload: {
        agent_id: 'agent-1',
        agent_name: 'Alice',
        task_id: 'task-1',
        before_tokens: 600,
        after_tokens: 120,
        max_tokens: 200,
        trim_tier: 2,
        budget_met: true,
      },
    })

    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('does not dispatch a toast when token fields are missing', () => {
    useAgentsStore.getState().updateFromWsEvent({
      event_type: 'personality.trimmed',
      channel: 'agents',
      timestamp: '2026-03-27T10:00:00Z',
      payload: { agent_name: 'Bob' },
    })

    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('suppresses the toast when every payload field is missing', () => {
    useAgentsStore.getState().updateFromWsEvent({
      event_type: 'personality.trimmed',
      channel: 'agents',
      timestamp: '2026-03-27T10:00:00Z',
      payload: {},
    })

    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('does not affect runtimeStatuses', () => {
    useAgentsStore.getState().updateFromWsEvent({
      event_type: 'personality.trimmed',
      channel: 'agents',
      timestamp: '2026-03-27T10:00:00Z',
      payload: { agent_name: 'Alice', before_tokens: 600, after_tokens: 120 },
    })
    expect(Object.keys(useAgentsStore.getState().runtimeStatuses)).toHaveLength(0)
  })

  it('does not dispatch a toast for long agent_name', () => {
    const longName = 'A'.repeat(200)
    useAgentsStore.getState().updateFromWsEvent({
      event_type: 'personality.trimmed',
      channel: 'agents',
      timestamp: '2026-03-27T10:00:00Z',
      payload: {
        agent_name: longName,
        before_tokens: 600,
        after_tokens: 120,
      },
    })

    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('does not dispatch a toast for non-finite token values', () => {
    useAgentsStore.getState().updateFromWsEvent({
      event_type: 'personality.trimmed',
      channel: 'agents',
      timestamp: '2026-03-27T10:00:00Z',
      payload: {
        agent_name: 'Carol',
        before_tokens: Number.POSITIVE_INFINITY,
        after_tokens: Number.NaN,
      },
    })

    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('does not dispatch a toast for non-string agent_name', () => {
    useAgentsStore.getState().updateFromWsEvent({
      event_type: 'personality.trimmed',
      channel: 'agents',
      timestamp: '2026-03-27T10:00:00Z',
      payload: {
        agent_name: 12345,
        before_tokens: 600,
        after_tokens: 120,
      },
    })

    expect(useToastStore.getState().toasts).toHaveLength(0)
  })
})
