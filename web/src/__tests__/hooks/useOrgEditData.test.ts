import { renderHook, waitFor } from '@testing-library/react'
import { useCompanyStore } from '@/stores/company'
import { useOrgEditData } from '@/hooks/useOrgEditData'
import type { WsEvent } from '@/api/types/websocket'
import { makeCompanyConfig, makeDepartmentHealth } from '../helpers/factories'

const mockFetchCompanyData = vi.fn().mockResolvedValue(undefined)
const mockFetchDepartmentHealths = vi.fn().mockResolvedValue(undefined)
const mockUpdateFromWsEvent = vi.fn<(event: WsEvent) => boolean>()
const mockUpdateCompany = vi.fn().mockResolvedValue(undefined)
const mockCreateDepartment = vi.fn()
const mockUpdateDepartment = vi.fn()
const mockDeleteDepartment = vi.fn().mockResolvedValue(undefined)
const mockReorderDepartments = vi.fn().mockResolvedValue(undefined)
const mockCreateAgent = vi.fn()
const mockUpdateAgent = vi.fn()
const mockDeleteAgent = vi.fn().mockResolvedValue(undefined)
const mockReorderAgents = vi.fn().mockResolvedValue(undefined)
const mockOptReorderDepts = vi.fn().mockReturnValue(() => {})
const mockOptReorderAgents = vi.fn().mockReturnValue(() => {})

const { mockPollingStart, mockPollingStop } = vi.hoisted(() => ({
  mockPollingStart: vi.fn(),
  mockPollingStop: vi.fn(),
}))

vi.mock('@/hooks/useWebSocket', () => ({
  useWebSocket: vi.fn().mockReturnValue({
    connected: true,
    reconnectExhausted: false,
    setupError: null,
  }),
}))

vi.mock('@/hooks/usePolling', () => ({
  usePolling: vi.fn().mockReturnValue({
    active: false,
    error: null,
    start: mockPollingStart,
    stop: mockPollingStop,
  }),
}))

function resetStore() {
  useCompanyStore.setState({
    config: null,
    departmentHealths: [],
    loading: false,
    error: null,
    healthError: null,
    savingCount: 0,
    saveError: null,
    fetchCompanyData: mockFetchCompanyData,
    fetchDepartmentHealths: mockFetchDepartmentHealths,
    updateFromWsEvent: mockUpdateFromWsEvent,
    updateCompany: mockUpdateCompany,
    createDepartment: mockCreateDepartment,
    updateDepartment: mockUpdateDepartment,
    deleteDepartment: mockDeleteDepartment,
    reorderDepartments: mockReorderDepartments,
    createAgent: mockCreateAgent,
    updateAgent: mockUpdateAgent,
    deleteAgent: mockDeleteAgent,
    reorderAgents: mockReorderAgents,
    optimisticReorderDepartments: mockOptReorderDepts,
    optimisticReorderAgents: mockOptReorderAgents,
  })
}

describe('useOrgEditData', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUpdateFromWsEvent.mockReturnValue(false)
    resetStore()
  })

  describe('freshness gating', () => {
    function frame(eventType: WsEvent['event_type']): WsEvent {
      return {
        event_type: eventType,
        channel: 'agents',
        timestamp: '2026-08-09T10:00:00Z',
        payload: {},
      }
    }

    async function deliverAndAskSkip(event: WsEvent): Promise<boolean> {
      const { useWebSocket } = await import('@/hooks/useWebSocket')
      const { usePolling } = await import('@/hooks/usePolling')
      renderHook(() => useOrgEditData())
      for (const binding of vi.mocked(useWebSocket).mock.calls[0]![0].bindings) {
        binding.handler(event)
      }
      return vi.mocked(usePolling).mock.calls[0]![2]!.skipIfFresh!()
    }

    it('skips the next poll after a frame that refetched company data', async () => {
      mockUpdateFromWsEvent.mockReturnValue(true)
      expect(await deliverAndAskSkip(frame('department.updated'))).toBe(true)
    })

    it('keeps polling through a frame the store ignored', async () => {
      // The store refetched nothing, so there is no fresher data to skip on.
      expect(await deliverAndAskSkip(frame('agent.status_changed'))).toBe(false)
    })
  })

  it('calls fetchCompanyData on mount and starts polling', async () => {
    renderHook(() => useOrgEditData())
    await waitFor(() => {
      expect(mockFetchCompanyData).toHaveBeenCalledTimes(1)
    })
    expect(mockPollingStart).toHaveBeenCalled()
  })

  it('calls fetchDepartmentHealths after config is available', async () => {
    mockFetchCompanyData.mockImplementationOnce(() => {
      useCompanyStore.setState({ config: makeCompanyConfig() })
      return Promise.resolve()
    })
    renderHook(() => useOrgEditData())
    await waitFor(() => {
      expect(mockFetchDepartmentHealths).toHaveBeenCalledTimes(1)
    })
  })

  it('stops polling on unmount', async () => {
    const { unmount } = renderHook(() => useOrgEditData())
    await waitFor(() => {
      expect(mockFetchCompanyData).toHaveBeenCalledTimes(1)
    })
    unmount()
    expect(mockPollingStop).toHaveBeenCalled()
  })

  it('returns loading state from store', () => {
    useCompanyStore.setState({ loading: true })
    const { result } = renderHook(() => useOrgEditData())
    expect(result.current.loading).toBe(true)
  })

  it('returns config from store', () => {
    const config = makeCompanyConfig()
    useCompanyStore.setState({ config })
    const { result } = renderHook(() => useOrgEditData())
    expect(result.current.config).toEqual(config)
  })

  it('returns departmentHealths from store', () => {
    const healths = [makeDepartmentHealth('engineering')]
    useCompanyStore.setState({ departmentHealths: healths })
    const { result } = renderHook(() => useOrgEditData())
    expect(result.current.departmentHealths).toEqual(healths)
  })

  it('returns error from store', () => {
    useCompanyStore.setState({ error: 'Something failed' })
    const { result } = renderHook(() => useOrgEditData())
    expect(result.current.error).toBe('Something failed')
  })

  it('returns saving and saveError from store', () => {
    useCompanyStore.setState({ savingCount: 1, saveError: 'Save failed' })
    const { result } = renderHook(() => useOrgEditData())
    expect(result.current.saving).toBe(true)
    expect(result.current.saveError).toBe('Save failed')
  })

  it('returns wsConnected as true', () => {
    const { result } = renderHook(() => useOrgEditData())
    expect(result.current.wsConnected).toBe(true)
  })

  describe('wsSetupError passthrough', () => {
    afterEach(async () => {
      const { useWebSocket } = await import('@/hooks/useWebSocket')
      vi.mocked(useWebSocket).mockReturnValue({
        connected: true,
        reconnectExhausted: false,
        setupError: null,
      })
    })

    it('returns wsSetupError from WebSocket hook', async () => {
      const { useWebSocket } = await import('@/hooks/useWebSocket')
      vi.mocked(useWebSocket).mockReturnValue({
        connected: false,
        reconnectExhausted: false,
        setupError: 'Auth token expired',
      })
      const { result } = renderHook(() => useOrgEditData())
      expect(result.current.wsSetupError).toBe('Auth token expired')
    })
  })

  it('exposes mutation functions wired to the store', () => {
    const { result } = renderHook(() => useOrgEditData())
    expect(result.current.updateCompany).toBe(mockUpdateCompany)
    expect(result.current.createDepartment).toBe(mockCreateDepartment)
    expect(result.current.updateDepartment).toBe(mockUpdateDepartment)
    expect(result.current.deleteDepartment).toBe(mockDeleteDepartment)
    expect(result.current.reorderDepartments).toBe(mockReorderDepartments)
    expect(result.current.createAgent).toBe(mockCreateAgent)
    expect(result.current.updateAgent).toBe(mockUpdateAgent)
    expect(result.current.deleteAgent).toBe(mockDeleteAgent)
    expect(result.current.reorderAgents).toBe(mockReorderAgents)
    expect(result.current.optimisticReorderDepartments).toBe(mockOptReorderDepts)
    expect(result.current.optimisticReorderAgents).toBe(mockOptReorderAgents)
  })
})
