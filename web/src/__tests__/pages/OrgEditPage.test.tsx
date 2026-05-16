import { act, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import type { UseOrgEditDataReturn } from '@/hooks/useOrgEditData'
import { useWebSocketStore } from '@/stores/websocket'
import { makeCompanyConfig, makeDepartmentHealth } from '../helpers/factories'

const noopAsync = vi.fn().mockResolvedValue(undefined)
const noopRollback = vi.fn().mockReturnValue(() => {})

const defaultHookReturn: UseOrgEditDataReturn = {
  config: makeCompanyConfig(),
  departmentHealths: [makeDepartmentHealth('engineering')],
  loading: false,
  error: null,
  saving: false,
  saveError: null,
  wsConnected: true,
  wsSetupError: null,
  updateCompany: noopAsync,
  createDepartment: noopAsync,
  updateDepartment: noopAsync,
  deleteDepartment: noopAsync,
  reorderDepartments: noopAsync,
  createAgent: noopAsync,
  updateAgent: noopAsync,
  deleteAgent: noopAsync,
  reorderAgents: noopAsync,
  createTeam: noopAsync,
  updateTeam: noopAsync,
  deleteTeam: noopAsync,
  reorderTeams: noopAsync,
  optimisticReorderDepartments: noopRollback,
  optimisticReorderAgents: noopRollback,
}

let hookReturn = { ...defaultHookReturn }

const getOrgEditData = vi.fn(() => hookReturn)
vi.mock('@/hooks/useOrgEditData', () => {
  const hookName = 'useOrgEditData'
  return { [hookName]: () => getOrgEditData() }
})

// Must import after vi.mock
import OrgEditPage from '@/pages/OrgEditPage'

function renderPage(initialPath = '/org/edit') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <OrgEditPage />
    </MemoryRouter>,
  )
}

describe('OrgEditPage', () => {
  beforeEach(() => {
    hookReturn = { ...defaultHookReturn }
    // WsConnectionBanner reads connected state from the WS store
    // directly; reset to a fresh disconnected baseline each test so
    // the "ever-connected" suppression behaves predictably.
    useWebSocketStore.setState({ connected: false })
    vi.clearAllMocks()
  })

  it('renders page heading', () => {
    renderPage()
    expect(screen.getByText('Edit Organization')).toBeInTheDocument()
  })

  it('renders Back to Org Chart link', () => {
    renderPage()
    expect(screen.getByLabelText('Back to Org Chart')).toBeInTheDocument()
  })

  it('renders tab triggers', () => {
    renderPage()
    expect(screen.getByText('General')).toBeInTheDocument()
    expect(screen.getByText('Agents')).toBeInTheDocument()
    expect(screen.getByText('Departments')).toBeInTheDocument()
  })

  it('renders loading skeleton when loading with no config', () => {
    hookReturn = { ...defaultHookReturn, config: null, loading: true }
    renderPage()
    expect(screen.getByLabelText('Loading organization editor')).toBeInTheDocument()
  })

  it('renders error banner when error is present', () => {
    hookReturn = { ...defaultHookReturn, error: 'Network failure' }
    renderPage()
    expect(screen.getByText('Network failure')).toBeInTheDocument()
  })

  it('renders save error banner', () => {
    hookReturn = { ...defaultHookReturn, saveError: 'Save failed' }
    renderPage()
    expect(screen.getByText('Save failed')).toBeInTheDocument()
  })

  it('renders WS disconnect warning after a prior connection drops', () => {
    // Start connected so WsConnectionBanner's internal "ever-connected"
    // ref flips to true; then drop the WS store's connected flag and
    // assert the banner now renders. The "ever-connected" gate is the
    // mechanism that suppresses the false-positive flash on initial
    // handshake (see the sibling test below).
    useWebSocketStore.setState({ connected: true })
    const { rerender } = renderPage()
    useWebSocketStore.setState({ connected: false })
    rerender(
      <MemoryRouter initialEntries={['/org/edit']}>
        <OrgEditPage />
      </MemoryRouter>,
    )
    expect(screen.getByText(/disconnected/i)).toBeInTheDocument()
  })

  it('does not render WS disconnect warning during initial handshake', () => {
    // Fresh mount with connected=false from the start (never connected):
    // banner must stay hidden to avoid the false-positive flash before
    // the WS finishes connecting. The grace timer is real, so a
    // synchronous render lands inside the suppression window.
    useWebSocketStore.setState({ connected: false })
    renderPage()
    expect(screen.queryByText(/disconnected/i)).not.toBeInTheDocument()
  })

  it('renders custom WS setup error on the "never connected" path', () => {
    // Truly exercises the never-connected branch: socket starts
    // (and stays) disconnected, ``wsSetupError`` is set, and we
    // advance past the initial-handshake grace window so the banner
    // surfaces with the supplied error in its description slot.
    vi.useFakeTimers()
    try {
      hookReturn = { ...defaultHookReturn, wsSetupError: 'Auth failed' }
      useWebSocketStore.setState({ connected: false })
      renderPage()
      // Past the 5s initial-handshake grace; the banner should now
      // be visible with the wsSetupError text.
      act(() => {
        vi.advanceTimersByTime(5001)
      })
      expect(screen.getByText('Auth failed')).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('renders YAML toggle', () => {
    renderPage()
    expect(screen.getByText('YAML')).toBeInTheDocument()
  })

  it('shows General tab content by default', () => {
    renderPage()
    // GeneralTab renders company settings section
    expect(screen.getByText('Company Settings')).toBeInTheDocument()
  })
})
