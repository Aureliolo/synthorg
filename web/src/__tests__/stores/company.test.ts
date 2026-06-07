import { describe, expect, it, beforeEach, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { useCompanyStore } from '@/stores/company'
import { apiError, apiSuccess, voidSuccess } from '@/mocks/handlers'
import { server } from '@/test-setup'
import type { DepartmentHealth } from '@/api/types/analytics'
import type { CompanyConfig } from '@/api/types/org'
import { makeAgent, makeCompanyConfig, makeDepartment } from '../helpers/factories'

const mockConfig: CompanyConfig = {
  company_name: 'Test Corp',
  agents: [],
  departments: [makeDepartment('engineering')],
}

const mockDeptHealth: DepartmentHealth = {
  department_name: 'engineering',
  agent_count: 3,
  active_agent_count: 2,
  currency: 'EUR',
  avg_performance_score: 7.5,
  department_cost_7d: 12.5,
  cost_trend: [],
  collaboration_score: 6.0,
  utilization_percent: 85,
}

function resetStore() {
  useCompanyStore.setState({
    config: null,
    departmentHealths: [],
    loading: false,
    error: null,
    healthError: null,
    savingCount: 0,
    saveError: null,
  })
}

describe('useCompanyStore', () => {
  beforeEach(() => {
    resetStore()
  })

  it('starts with null config and empty health', () => {
    const state = useCompanyStore.getState()
    expect(state.config).toBeNull()
    expect(state.departmentHealths).toEqual([])
    expect(state.loading).toBe(false)
    expect(state.error).toBeNull()
    expect(state.healthError).toBeNull()
    expect(state.savingCount).toBe(0)
    expect(state.saveError).toBeNull()
  })

  it('fetchCompanyData sets config on success', async () => {
    server.use(
      http.get('/api/v1/company', () =>
        HttpResponse.json(apiSuccess(mockConfig)),
      ),
    )
    await useCompanyStore.getState().fetchCompanyData()
    const state = useCompanyStore.getState()
    expect(state.config).toEqual(mockConfig)
    expect(state.loading).toBe(false)
    expect(state.error).toBeNull()
  })

  it('fetchCompanyData sets error on failure and rethrows', async () => {
    server.use(
      http.get('/api/v1/company', () =>
        HttpResponse.json(apiError('Network error')),
      ),
    )
    await expect(
      useCompanyStore.getState().fetchCompanyData(),
    ).rejects.toThrow('Network error')
    const state = useCompanyStore.getState()
    expect(state.config).toBeNull()
    expect(state.loading).toBe(false)
    expect(state.error).toBe('Network error')
  })

  it('fetchDepartmentHealths populates array on success', async () => {
    useCompanyStore.setState({ config: mockConfig })
    server.use(
      http.get('/api/v1/departments/:name/health', () =>
        HttpResponse.json(apiSuccess(mockDeptHealth)),
      ),
    )

    await useCompanyStore.getState().fetchDepartmentHealths()
    expect(useCompanyStore.getState().departmentHealths).toEqual([mockDeptHealth])
  })

  it('fetchDepartmentHealths does nothing without config', async () => {
    let called = false
    server.use(
      http.get('/api/v1/departments/:name/health', () => {
        called = true
        return HttpResponse.json(apiSuccess(mockDeptHealth))
      }),
    )
    await useCompanyStore.getState().fetchDepartmentHealths()
    expect(called).toBe(false)
  })

  it('fetchDepartmentHealths sets healthError when all fetches fail', async () => {
    useCompanyStore.setState({ config: mockConfig })
    server.use(
      http.get('/api/v1/departments/:name/health', () =>
        HttpResponse.json(apiError('Service down')),
      ),
    )

    await useCompanyStore.getState().fetchDepartmentHealths()
    const state = useCompanyStore.getState()
    expect(state.departmentHealths).toEqual([])
    expect(state.healthError).toBe('Failed to fetch department health data')
  })

  it('fetchDepartmentHealths clears healthError on success', async () => {
    useCompanyStore.setState({
      config: mockConfig,
      healthError: 'previous error',
    })
    server.use(
      http.get('/api/v1/departments/:name/health', () =>
        HttpResponse.json(apiSuccess(mockDeptHealth)),
      ),
    )

    await useCompanyStore.getState().fetchDepartmentHealths()
    expect(useCompanyStore.getState().healthError).toBeNull()
  })

  it('fetchDepartmentHealths filters out failed health fetches', async () => {
    const configWithTwoDepts: CompanyConfig = {
      ...mockConfig,
      departments: [
        makeDepartment('engineering'),
        makeDepartment('product'),
      ],
    }
    useCompanyStore.setState({ config: configWithTwoDepts })
    server.use(
      http.get('/api/v1/departments/:name/health', ({ params }) => {
        if (params.name === 'engineering') {
          return HttpResponse.json(apiSuccess(mockDeptHealth))
        }
        return HttpResponse.json(apiError('Not found'))
      }),
    )

    await useCompanyStore.getState().fetchDepartmentHealths()
    const healths = useCompanyStore.getState().departmentHealths
    expect(healths).toHaveLength(1)
    expect(healths[0]!.department_name).toBe('engineering')
  })

  it('updateFromWsEvent triggers re-fetch of config and health on agent.hired', async () => {
    let configCalls = 0
    let healthCalls = 0
    server.use(
      http.get('/api/v1/company', () => {
        configCalls += 1
        return HttpResponse.json(apiSuccess(mockConfig))
      }),
      http.get('/api/v1/departments/:name/health', () => {
        healthCalls += 1
        return HttpResponse.json(apiSuccess(mockDeptHealth))
      }),
    )
    useCompanyStore.getState().updateFromWsEvent({
      event_type: 'agent.hired',
      channel: 'agents',
      timestamp: '2026-03-27T10:00:00Z',
      payload: {},
    })
    await vi.waitFor(() => {
      expect(configCalls).toBeGreaterThan(0)
    })
    await vi.waitFor(() => {
      expect(healthCalls).toBeGreaterThan(0)
    })
  })

  it('updateFromWsEvent triggers re-fetch of config and health on agent.fired', async () => {
    let configCalls = 0
    let healthCalls = 0
    server.use(
      http.get('/api/v1/company', () => {
        configCalls += 1
        return HttpResponse.json(apiSuccess(mockConfig))
      }),
      http.get('/api/v1/departments/:name/health', () => {
        healthCalls += 1
        return HttpResponse.json(apiSuccess(mockDeptHealth))
      }),
    )
    useCompanyStore.getState().updateFromWsEvent({
      event_type: 'agent.fired',
      channel: 'agents',
      timestamp: '2026-03-27T10:00:00Z',
      payload: {},
    })
    await vi.waitFor(() => {
      expect(configCalls).toBeGreaterThan(0)
    })
    await vi.waitFor(() => {
      expect(healthCalls).toBeGreaterThan(0)
    })
  })

  it('updateFromWsEvent ignores unrelated events', async () => {
    let configCalls = 0
    server.use(
      http.get('/api/v1/company', () => {
        configCalls += 1
        return HttpResponse.json(apiSuccess(mockConfig))
      }),
    )
    useCompanyStore.getState().updateFromWsEvent({
      event_type: 'task.created',
      channel: 'tasks',
      timestamp: '2026-03-27T10:00:00Z',
      payload: {},
    })
    // Drain the microtask queue; a refetch would be scheduled on the next
    // microtask, so asserting after a single Promise.resolve() reliably
    // catches any unintended fetch while still being a true negative test.
    await Promise.resolve()
    expect(configCalls).toBe(0)
  })

  describe('updateCompany', () => {
    it('updates config on success', async () => {
      const updated = { ...mockConfig, company_name: 'New Name' }
      let configFetched = false
      server.use(
        http.patch('/api/v1/company', () =>
          HttpResponse.json(apiSuccess({ company_name: 'New Name' })),
        ),
        http.get('/api/v1/company', () => {
          configFetched = true
          return HttpResponse.json(apiSuccess(updated))
        }),
      )
      useCompanyStore.setState({ config: mockConfig })

      await useCompanyStore.getState().updateCompany({ company_name: 'New Name', autonomy_level: null })
      expect(useCompanyStore.getState().config?.company_name).toBe('New Name')
      expect(useCompanyStore.getState().savingCount).toBe(0)
      expect(configFetched).toBe(true)
    })

    it('returns false sentinel + emits error toast on failure', async () => {
      const { useToastStore } = await import('@/stores/toast')
      useToastStore.getState().dismissAll()
      server.use(
        http.patch('/api/v1/company', () =>
          HttpResponse.json(apiError('Forbidden'), { status: 403 }),
        ),
      )
      useCompanyStore.setState({ config: mockConfig })

      const result = await useCompanyStore
        .getState()
        .updateCompany({ company_name: 'X', autonomy_level: null })

      expect(result).toBe(false)
      expect(useCompanyStore.getState().saveError).toContain('Forbidden')
      expect(useCompanyStore.getState().savingCount).toBe(0)
      const toasts = useToastStore.getState().toasts
      expect(toasts).toHaveLength(1)
      expect(toasts[0]!.variant).toBe('error')
      // updateCompany routes through getCrudErrorTitle so a 403 lands
      // on the dedicated "Permission denied" title rather than the
      // generic fallback. Other CRUD tests assert similar specialised
      // titles for their distinct categories.
      expect(toasts[0]!.title).toBe('Permission denied')
    })
  })

  describe('createDepartment', () => {
    it('appends new department to config', async () => {
      const newDept = makeDepartment('design')
      server.use(
        http.post('/api/v1/departments', () =>
          HttpResponse.json(apiSuccess(newDept), { status: 201 }),
        ),
      )
      useCompanyStore.setState({ config: mockConfig })

      const result = await useCompanyStore
        .getState()
        .createDepartment({ name: 'design', autonomy_level: null, budget_percent: 0 })
      expect(result).toEqual(newDept)
      expect(useCompanyStore.getState().config!.departments).toHaveLength(2)
    })

    it('returns null on failure, emits toast, and leaves config untouched', async () => {
      const { useToastStore } = await import('@/stores/toast')
      useToastStore.getState().dismissAll()
      server.use(
        http.post('/api/v1/departments', () =>
          HttpResponse.json(apiError('Conflict'), { status: 409 }),
        ),
      )
      useCompanyStore.setState({ config: mockConfig })

      const result = await useCompanyStore
        .getState()
        .createDepartment({ name: 'x', autonomy_level: null, budget_percent: 0 })
      expect(result).toBeNull()
      expect(useCompanyStore.getState().config!.departments).toHaveLength(1)
      // The sentinel contract pairs with an error toast; assert it surfaced.
      const toasts = useToastStore.getState().toasts
      expect(toasts.length).toBeGreaterThan(0)
      expect(toasts[0]!.variant).toBe('error')
    })
  })

  describe('updateDepartment', () => {
    it('replaces department in config', async () => {
      const updated = makeDepartment('engineering', {
        display_name: 'Eng Team',
        budget_percent: 50,
      })
      server.use(
        http.patch('/api/v1/departments/:name', () =>
          HttpResponse.json(apiSuccess(updated)),
        ),
      )
      useCompanyStore.setState({ config: mockConfig })

      const result = await useCompanyStore
        .getState()
        .updateDepartment('engineering', { budget_percent: 50, autonomy_level: null })
      expect(result).not.toBeNull()
      expect(result!.name).toBe('engineering')
      expect(result!.budget_percent).toBe(50)
      expect(useCompanyStore.getState().config!.departments[0]!.name).toBe(
        'engineering',
      )
      expect(
        useCompanyStore.getState().config!.departments[0]!.budget_percent,
      ).toBe(50)
    })
  })

  describe('deleteDepartment', () => {
    it('removes department from config', async () => {
      server.use(
        http.delete('/api/v1/departments/:name', () =>
          HttpResponse.json(voidSuccess()),
        ),
      )
      useCompanyStore.setState({ config: mockConfig })

      await useCompanyStore.getState().deleteDepartment('engineering')
      expect(useCompanyStore.getState().config!.departments).toHaveLength(0)
    })
  })

  describe('reorderDepartments', () => {
    it('updates config with reordered result', async () => {
      const reordered = [makeDepartment('product'), makeDepartment('engineering')]
      server.use(
        http.post('/api/v1/company/reorder-departments', () =>
          HttpResponse.json(apiSuccess(reordered)),
        ),
      )
      useCompanyStore.setState({ config: mockConfig })

      await useCompanyStore
        .getState()
        .reorderDepartments(['product', 'engineering'])
      expect(useCompanyStore.getState().config!.departments[0]!.name).toBe(
        'product',
      )
    })

    it('returns false sentinel + sets saveError on failure', async () => {
      server.use(
        http.post('/api/v1/company/reorder-departments', () =>
          HttpResponse.json(apiError('Reorder denied'), { status: 400 }),
        ),
      )
      useCompanyStore.setState({ config: mockConfig })

      const result = await useCompanyStore
        .getState()
        .reorderDepartments(['product', 'engineering'])
      expect(result).toBe(false)
      expect(useCompanyStore.getState().saveError).toBe('Reorder denied')
      expect(useCompanyStore.getState().savingCount).toBe(0)
    })
  })

  describe('createAgent', () => {
    it('appends new agent to config', async () => {
      const newAgent = makeAgent('dave')
      server.use(
        http.post('/api/v1/agents', () =>
          HttpResponse.json(apiSuccess(newAgent), { status: 201 }),
        ),
      )
      useCompanyStore.setState({ config: mockConfig })

      const result = await useCompanyStore.getState().createAgent({
        name: 'dave',
        role: 'Designer',
        department: 'engineering',
        level: 'mid',
      })
      expect(result).toEqual(newAgent)
      expect(useCompanyStore.getState().config!.agents).toHaveLength(1)
    })
  })

  describe('updateAgent', () => {
    it('replaces agent in config', async () => {
      const agent = makeAgent('alice')
      const updated = { ...agent, role: 'Senior Dev' }
      server.use(
        http.patch('/api/v1/agents/:name', () =>
          HttpResponse.json(apiSuccess(updated)),
        ),
      )
      useCompanyStore.setState({
        config: { ...mockConfig, agents: [agent] },
      })

      const result = await useCompanyStore
        .getState()
        .updateAgent('agent-alice', { role: 'Senior Dev', autonomy_level: null, level: null })
      expect(result).not.toBeNull()
      expect(result!.role).toBe('Senior Dev')
    })
  })

  describe('deleteAgent', () => {
    it('removes agent from config', async () => {
      const agent = makeAgent('alice')
      server.use(
        http.delete('/api/v1/agents/:name', () =>
          HttpResponse.json(voidSuccess()),
        ),
      )
      useCompanyStore.setState({
        config: { ...mockConfig, agents: [agent] },
      })

      await useCompanyStore.getState().deleteAgent('agent-alice')
      expect(useCompanyStore.getState().config!.agents).toHaveLength(0)
    })
  })

  describe('reorderAgents', () => {
    it('calls API and clears saving flag', async () => {
      let capturedBody: unknown = null
      let configFetched = false
      server.use(
        http.post(
          '/api/v1/departments/:name/reorder-agents',
          async ({ request }) => {
            capturedBody = await request.json()
            return HttpResponse.json(apiSuccess(mockConfig.agents))
          },
        ),
        http.get('/api/v1/company', () => {
          configFetched = true
          return HttpResponse.json(apiSuccess(mockConfig))
        }),
      )
      useCompanyStore.setState({ config: mockConfig })

      await useCompanyStore
        .getState()
        .reorderAgents('engineering', ['a-2', 'a-1'])
      expect(capturedBody).toEqual({ agent_names: ['a-2', 'a-1'] })
      expect(useCompanyStore.getState().savingCount).toBe(0)
      expect(configFetched).toBe(true)
    })

    it('returns false sentinel + sets saveError on failure', async () => {
      server.use(
        http.post('/api/v1/departments/:name/reorder-agents', () =>
          HttpResponse.json(apiError('Reorder failed'), { status: 400 }),
        ),
      )
      useCompanyStore.setState({ config: mockConfig })

      const result = await useCompanyStore
        .getState()
        .reorderAgents('engineering', ['a-2', 'a-1'])
      expect(result).toBe(false)
      expect(useCompanyStore.getState().saveError).toBe('Reorder failed')
      expect(useCompanyStore.getState().savingCount).toBe(0)
    })
  })

  describe('optimisticReorderDepartments', () => {
    it('reorders departments and returns rollback', () => {
      const config = makeCompanyConfig()
      useCompanyStore.setState({ config })

      const rollback = useCompanyStore
        .getState()
        .optimisticReorderDepartments(['product', 'engineering'])
      expect(useCompanyStore.getState().config!.departments[0]!.name).toBe(
        'product',
      )

      rollback()
      expect(useCompanyStore.getState().config!.departments[0]!.name).toBe(
        'engineering',
      )
    })

    it('returns no-op when config is null', () => {
      const rollback = useCompanyStore
        .getState()
        .optimisticReorderDepartments(['a'])
      expect(rollback).toBeTypeOf('function')
      rollback()
    })
  })

  describe('optimisticReorderAgents', () => {
    it('reorders agents within department and returns rollback', () => {
      const config = makeCompanyConfig()
      useCompanyStore.setState({ config })

      const agentIds = config.agents
        .filter((a) => a.department === 'engineering')
        .map((a) => a.id)
        .reverse()

      const rollback = useCompanyStore
        .getState()
        .optimisticReorderAgents('engineering', agentIds)

      const reordered = useCompanyStore
        .getState()
        .config!.agents.filter((a) => a.department === 'engineering')
      expect(reordered.map((a) => a.id)).toEqual(agentIds)

      rollback()
      const restored = useCompanyStore
        .getState()
        .config!.agents.filter((a) => a.department === 'engineering')
      expect(restored.map((a) => a.id)).toEqual(agentIds.toReversed())
    })
  })
})
