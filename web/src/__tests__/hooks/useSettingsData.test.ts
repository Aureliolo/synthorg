import { renderHook, waitFor } from '@testing-library/react'
import { useSettingsStore } from '@/stores/settings'
import { useSettingsData } from '@/hooks/useSettingsData'

const mockFetchSettingsData = vi.fn().mockResolvedValue(undefined)
const mockRefreshEntries = vi.fn().mockResolvedValue(undefined)
const mockUpdateSetting = vi.fn().mockResolvedValue({
  definition: { namespace: 'api', key: 'server_port', type: 'int', default: '3001', description: 'Server bind port', group: 'Server', level: 'basic', sensitive: false, restart_required: true, read_only_post_init: false, env_var_override: null, enum_values: [], validator_pattern: null, min_value: 1, max_value: 65535 },
  value: '3001', source: 'db', updated_at: null,
})
const mockResetSetting = vi.fn().mockResolvedValue(undefined)
const mockUpdateFromWsEvent = vi.fn()
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
  useSettingsStore.setState({
    schema: [],
    entries: [],
    loading: false,
    error: null,
    savingKeys: new Map(),
    saveError: null,
    fetchSettingsData: mockFetchSettingsData,
    refreshEntries: mockRefreshEntries,
    updateSetting: mockUpdateSetting,
    resetSetting: mockResetSetting,
    updateFromWsEvent: mockUpdateFromWsEvent,
  })
}

describe('useSettingsData', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    resetStore()
  })

  it('calls fetchSettingsData on mount', async () => {
    renderHook(() => useSettingsData())
    await waitFor(() => {
      expect(mockFetchSettingsData).toHaveBeenCalledTimes(1)
    })
  })

  it('returns loading state from store', () => {
    useSettingsStore.setState({ loading: true })
    const { result } = renderHook(() => useSettingsData())
    expect(result.current.loading).toBe(true)
  })

  it('returns entries from store', () => {
    const mockEntries = [
      {
        definition: {
          namespace: 'api' as const,
          key: 'server_port',
          type: 'int' as const,
          default: '3001',
          description: 'Server bind port',
          group: 'Server',
          level: 'basic' as const,
          sensitive: false,
          restart_required: true,
          read_only_post_init: false,
          env_var_override: null,
          enum_values: [],
          validator_pattern: null,
          min_value: 1,
          max_value: 65535,
        },
        value: '3001',
        source: 'default' as const,
        updated_at: null,
      },
    ]
    useSettingsStore.setState({ entries: mockEntries })
    const { result } = renderHook(() => useSettingsData())
    expect(result.current.entries).toEqual(mockEntries)
  })

  it('returns error from store', () => {
    useSettingsStore.setState({ error: 'Something broke' })
    const { result } = renderHook(() => useSettingsData())
    expect(result.current.error).toBe('Something broke')
  })

  it('derives saving from savingKeys size', () => {
    useSettingsStore.setState({ savingKeys: new Map([['api/server_port', 1]]) })
    const { result } = renderHook(() => useSettingsData())
    expect(result.current.saving).toBe(true)
  })

  it('returns saving=false when no keys are saving', () => {
    const { result } = renderHook(() => useSettingsData())
    expect(result.current.saving).toBe(false)
  })

  it('sets up WebSocket with system channel binding', async () => {
    const { useWebSocket } = await import('@/hooks/useWebSocket')
    renderHook(() => useSettingsData())

    const mock = vi.mocked(useWebSocket)
    expect(mock).toHaveBeenCalled()
    const callArgs = mock.mock.calls[0]![0]
    const channels = callArgs.bindings.map((b) => b.channel)
    expect(channels).toEqual(['system'])
  })

  it('returns wsConnected from useWebSocket', () => {
    const { result } = renderHook(() => useSettingsData())
    expect(result.current.wsConnected).toBe(true)
  })

  it('starts polling on mount', async () => {
    renderHook(() => useSettingsData())
    await waitFor(() => {
      expect(mockPollingStart).toHaveBeenCalledOnce()
    })
  })

  it('stops polling on unmount', () => {
    const { unmount } = renderHook(() => useSettingsData())
    unmount()
    expect(mockPollingStop).toHaveBeenCalledOnce()
  })
})
