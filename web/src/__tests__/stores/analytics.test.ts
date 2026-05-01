import { http, HttpResponse } from 'msw'
import { useAnalyticsStore } from '@/stores/analytics'
import type { DefaultBodyType, StrictResponse } from 'msw'
import { apiError, apiSuccess, paginatedFor } from '@/mocks/handlers'
import type { listActivities } from '@/api/endpoints/activities'
import type { listDepartments } from '@/api/endpoints/company'
import { server } from '@/test-setup'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import type { ActivityItem } from '@/api/types/analytics'
import type { Department } from '@/api/types/org'
import type { WsEvent } from '@/api/types/websocket'

const mockOverview = {
  total_tasks: 24,
  tasks_by_status: {
    created: 2,
    assigned: 3,
    in_progress: 8,
    in_review: 2,
    completed: 5,
    blocked: 1,
    failed: 1,
    interrupted: 1,
    suspended: 0,
    cancelled: 1,
  },
  total_agents: 10,
  total_cost: 42.17,
  budget_remaining: 457.83,
  budget_used_percent: 8.43,
  cost_7d_trend: [],
  active_agents_count: 5,
  idle_agents_count: 4,
  currency: DEFAULT_CURRENCY,
}

const mockForecast = {
  horizon_days: 30,
  projected_total: 200,
  daily_projections: [],
  days_until_exhausted: null,
  confidence: 0.85,
  avg_daily_spend: 6.5,
  currency: DEFAULT_CURRENCY,
}

const mockBudgetConfig = {
  total_monthly: 500,
  alerts: { warn_at: 80, critical_at: 95, hard_stop_at: 100 },
  per_task_limit: 10,
  per_agent_daily_limit: 20,
  auto_downgrade: {
    enabled: false,
    threshold: 90,
    downgrade_map: [],
    boundary: 'task_assignment',
  },
  reset_day: 1,
  currency: DEFAULT_CURRENCY,
}

const mockDeptHealth = {
  department_name: 'engineering',
  agent_count: 4,
  active_agent_count: 3,
  currency: DEFAULT_CURRENCY,
  avg_performance_score: 8.0,
  department_cost_7d: 10.0,
  cost_trend: [],
  collaboration_score: 7.0,
  utilization_percent: 85,
}

type HandlerReturn =
  | StrictResponse<DefaultBodyType>
  | Promise<StrictResponse<DefaultBodyType>>

type Overrides = Partial<{
  overview: () => HandlerReturn
  forecast: () => HandlerReturn
  budget: () => HandlerReturn
  activities: () => HandlerReturn
  departments: () => HandlerReturn
  departmentHealth: (name: string) => HandlerReturn
}>

function installDefaults(overrides: Overrides = {}) {
  server.use(
    http.get('/api/v1/analytics/overview', () =>
      overrides.overview
        ? overrides.overview()
        : HttpResponse.json(apiSuccess(mockOverview)),
    ),
    http.get('/api/v1/analytics/forecast', () =>
      overrides.forecast
        ? overrides.forecast()
        : HttpResponse.json(apiSuccess(mockForecast)),
    ),
    http.get('/api/v1/budget/config', () =>
      overrides.budget
        ? overrides.budget()
        : HttpResponse.json(apiSuccess(mockBudgetConfig)),
    ),
    http.get('/api/v1/activities', () =>
      overrides.activities
        ? overrides.activities()
        : HttpResponse.json(
            paginatedFor<typeof listActivities>({
              data: [] as ActivityItem[],
              limit: 20,
              nextCursor: null,
              hasMore: false,
              pagination: {
                limit: 20,
                next_cursor: null,
                has_more: false,
              },
            }),
          ),
    ),
    http.get('/api/v1/departments', () =>
      overrides.departments
        ? overrides.departments()
        : HttpResponse.json(
            paginatedFor<typeof listDepartments>({
              data: [] as Department[],
              limit: 50,
              nextCursor: null,
              hasMore: false,
              pagination: {
                limit: 50,
                next_cursor: null,
                has_more: false,
              },
            }),
          ),
    ),
    http.get('/api/v1/departments/:name/health', ({ params }) =>
      overrides.departmentHealth
        ? overrides.departmentHealth(String(params.name))
        : HttpResponse.json(apiSuccess(mockDeptHealth)),
    ),
  )
}

function resetStore() {
  useAnalyticsStore.setState({
    overview: null,
    forecast: null,
    departmentHealths: [],
    activities: [],
    budgetConfig: null,
    orgHealthPercent: null,
    loading: false,
    error: null,
  })
}

describe('useAnalyticsStore', () => {
  beforeEach(() => {
    resetStore()
  })

  describe('fetchDashboardData', () => {
    it('sets loading to true during fetch', async () => {
      installDefaults()
      const promise = useAnalyticsStore.getState().fetchDashboardData()
      expect(useAnalyticsStore.getState().loading).toBe(true)
      await promise
    })

    it('populates overview after fetch', async () => {
      installDefaults()
      await useAnalyticsStore.getState().fetchDashboardData()
      const state = useAnalyticsStore.getState()
      expect(state.overview).not.toBeNull()
      expect(state.overview!.total_tasks).toBe(24)
    })

    it('populates forecast after fetch', async () => {
      installDefaults()
      await useAnalyticsStore.getState().fetchDashboardData()
      expect(useAnalyticsStore.getState().forecast).not.toBeNull()
    })

    it('populates budgetConfig after fetch', async () => {
      installDefaults()
      await useAnalyticsStore.getState().fetchDashboardData()
      expect(useAnalyticsStore.getState().budgetConfig).not.toBeNull()
    })

    it('sets loading to false after fetch', async () => {
      installDefaults()
      await useAnalyticsStore.getState().fetchDashboardData()
      expect(useAnalyticsStore.getState().loading).toBe(false)
    })

    it('sets error to null on success', async () => {
      installDefaults()
      useAnalyticsStore.setState({ error: 'previous error' })
      await useAnalyticsStore.getState().fetchDashboardData()
      expect(useAnalyticsStore.getState().error).toBeNull()
    })

    it('degrades gracefully when listActivities fails', async () => {
      installDefaults({
        activities: () => HttpResponse.json(apiError('Not found')),
      })

      await useAnalyticsStore.getState().fetchDashboardData()
      const state = useAnalyticsStore.getState()
      expect(state.activities).toEqual([])
      expect(state.error).toBeNull()
    })

    it('degrades gracefully when listDepartments fails', async () => {
      installDefaults({
        departments: () => HttpResponse.json(apiError('Not found')),
      })

      await useAnalyticsStore.getState().fetchDashboardData()
      const state = useAnalyticsStore.getState()
      expect(state.departmentHealths).toEqual([])
      expect(state.error).toBeNull()
    })

    it('populates departmentHealths when departments exist', async () => {
      installDefaults({
        departments: () =>
          HttpResponse.json(
            paginatedFor<typeof listDepartments>({
              data: [
                {
                  name: 'engineering',
                  display_name: 'Engineering',
                  teams: [],
                } as Department,
              ],
              limit: 100,
              nextCursor: null,
              hasMore: false,
              pagination: {
                limit: 100,
                next_cursor: null,
                has_more: false,
              },
            }),
          ),
      })

      await useAnalyticsStore.getState().fetchDashboardData()
      const state = useAnalyticsStore.getState()
      expect(state.departmentHealths).toHaveLength(1)
      expect(state.departmentHealths[0]!.utilization_percent).toBe(85)
      expect(state.orgHealthPercent).toBe(85)
    })

    it('sets error when overview fails (critical dataset)', async () => {
      installDefaults({
        overview: () => HttpResponse.json(apiError('Network error')),
      })

      await useAnalyticsStore.getState().fetchDashboardData()
      const state = useAnalyticsStore.getState()
      expect(state.error).toBe('Network error')
      expect(state.loading).toBe(false)
    })

    it('degrades gracefully when forecast fails', async () => {
      installDefaults({
        forecast: () => HttpResponse.json(apiError('Forecast unavailable')),
      })

      await useAnalyticsStore.getState().fetchDashboardData()
      const state = useAnalyticsStore.getState()
      expect(state.overview).not.toBeNull()
      expect(state.forecast).toBeNull()
      expect(state.error).toBeNull()
    })

    it('degrades gracefully when budgetConfig fails', async () => {
      installDefaults({
        budget: () => HttpResponse.json(apiError('Budget unavailable')),
      })

      await useAnalyticsStore.getState().fetchDashboardData()
      const state = useAnalyticsStore.getState()
      expect(state.overview).not.toBeNull()
      expect(state.budgetConfig).toBeNull()
      expect(state.error).toBeNull()
    })

    it('filters out failed individual department health fetches', async () => {
      installDefaults({
        departments: () =>
          HttpResponse.json(
            paginatedFor<typeof listDepartments>({
              data: [
                { name: 'engineering', display_name: 'Engineering', teams: [] } as Department,
                { name: 'design', display_name: 'Design', teams: [] } as Department,
                { name: 'operations', display_name: 'Operations', teams: [] } as Department,
              ],
              limit: 100,
              nextCursor: null,
              hasMore: false,
              pagination: {
                limit: 100,
                next_cursor: null,
                has_more: false,
              },
            }),
          ),
        departmentHealth: (name: string) => {
          if (name === 'design') {
            return HttpResponse.json(apiError('Design health unavailable'))
          }
          if (name === 'engineering') {
            return HttpResponse.json(apiSuccess(mockDeptHealth))
          }
          return HttpResponse.json(
            apiSuccess({
              ...mockDeptHealth,
              department_name: 'operations',
              agent_count: 2,
              active_agent_count: 1,
              avg_performance_score: 6.0,
              department_cost_7d: 5.0,
              collaboration_score: null,
              utilization_percent: 70,
            }),
          )
        },
      })

      await useAnalyticsStore.getState().fetchDashboardData()
      const state = useAnalyticsStore.getState()
      expect(state.departmentHealths).toHaveLength(2)
      expect(state.error).toBeNull()
    })
  })

  describe('fetchOverview', () => {
    it('updates overview without resetting other state', async () => {
      installDefaults()
      const existingActivities: ActivityItem[] = [
        {
          id: '1',
          timestamp: '2026-03-26T10:00:00Z',
          agent_name: 'agent-a',
          action_type: 'task.created',
          description: 'test',
          task_id: null,
          department: null,
        },
      ]
      useAnalyticsStore.setState({ activities: existingActivities })

      await useAnalyticsStore.getState().fetchOverview()
      const state = useAnalyticsStore.getState()
      expect(state.overview).not.toBeNull()
      expect(state.activities).toEqual(existingActivities)
    })
  })

  describe('pushActivity', () => {
    it('prepends an activity to the list', () => {
      const item: ActivityItem = {
        id: 'new-1',
        timestamp: '2026-03-26T10:00:00Z',
        agent_name: 'agent-a',
        action_type: 'task.created',
        description: 'created a task',
        task_id: null,
        department: null,
      }
      useAnalyticsStore.getState().pushActivity(item)
      expect(useAnalyticsStore.getState().activities[0]).toEqual(item)
    })

    it('caps activities at 50 items', () => {
      const items: ActivityItem[] = Array.from({ length: 50 }, (_, i) => ({
        id: `existing-${i}`,
        timestamp: '2026-03-26T10:00:00Z',
        agent_name: 'agent',
        action_type: 'task.created' as const,
        description: 'test',
        task_id: null,
        department: null,
      }))
      useAnalyticsStore.setState({ activities: items })

      const newItem: ActivityItem = {
        id: 'new-51',
        timestamp: '2026-03-26T11:00:00Z',
        agent_name: 'agent-new',
        action_type: 'task.updated',
        description: 'updated',
        task_id: null,
        department: null,
      }
      useAnalyticsStore.getState().pushActivity(newItem)

      const activities = useAnalyticsStore.getState().activities
      expect(activities).toHaveLength(50)
      expect(activities[0]!.id).toBe('new-51')
      expect(activities[49]!.id).toBe('existing-48')
    })
  })

  describe('updateFromWsEvent', () => {
    it('pushes a new activity from the event', () => {
      const event: WsEvent = {
        event_type: 'task.created',
        channel: 'tasks',
        timestamp: '2026-03-26T10:00:00Z',
        payload: { agent_name: 'agent-cto', task_id: 'task-1' },
      }
      useAnalyticsStore.getState().updateFromWsEvent(event)
      const activities = useAnalyticsStore.getState().activities
      expect(activities).toHaveLength(1)
      expect(activities[0]!.agent_name).toBe('agent-cto')
    })
  })
})
