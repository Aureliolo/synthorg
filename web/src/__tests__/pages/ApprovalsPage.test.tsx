import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import ApprovalsPage from '@/pages/ApprovalsPage'
import { makeApproval } from '../helpers/factories'
import type { UseApprovalsDataReturn } from '@/hooks/useApprovalsData'

// Mutable hook return that tests can override
const defaultReturn: UseApprovalsDataReturn = {
  approvals: [],
  selectedApproval: null,
  total: 0,
  loading: false,
  loadingDetail: false,
  error: null,
  detailError: null,
  isRefetching: false,
  wsConnected: true,
  wsSetupError: null,
  fetchApproval: vi.fn(),
  approveOne: vi.fn().mockResolvedValue(undefined),
  rejectOne: vi.fn().mockResolvedValue(undefined),
  optimisticApprove: vi.fn().mockReturnValue(() => {}),
  optimisticReject: vi.fn().mockReturnValue(() => {}),
  selectedIds: new Set(),
  toggleSelection: vi.fn(),
  selectAllInGroup: vi.fn(),
  deselectAllInGroup: vi.fn(),
  clearSelection: vi.fn(),
  batchApprove: vi.fn().mockResolvedValue({ succeeded: 0, failed: 0, failedReasons: [] }),
  batchReject: vi.fn().mockResolvedValue({ succeeded: 0, failed: 0, failedReasons: [] }),
}

let hookReturn = { ...defaultReturn }
const getApprovalsData = vi.fn(() => hookReturn)

vi.mock('@/hooks/useApprovalsData', () => {
  const hookName = 'useApprovalsData'
  return { [hookName]: () => getApprovalsData() }
})

function renderPage() {
  return render(
    <MemoryRouter>
      <ApprovalsPage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  hookReturn = { ...defaultReturn, selectedIds: new Set() }
  vi.clearAllMocks()
})

describe('ApprovalsPage', () => {
  it('renders loading skeleton when loading with no data', () => {
    hookReturn = { ...defaultReturn, loading: true, approvals: [], selectedIds: new Set() }
    renderPage()
    expect(screen.getByLabelText('Loading approvals')).toBeInTheDocument()
  })

  it('renders page heading', () => {
    renderPage()
    expect(screen.getByRole('heading', { name: 'Approvals' })).toBeInTheDocument()
  })

  it('renders error banner when error exists', () => {
    hookReturn = { ...defaultReturn, error: 'Something went wrong', selectedIds: new Set() }
    renderPage()
    expect(screen.getByText('Something went wrong')).toBeInTheDocument()
  })

  it('renders WS disconnected banner when setup error', () => {
    hookReturn = { ...defaultReturn, wsConnected: false, wsSetupError: 'WebSocket connection failed.', selectedIds: new Set() }
    renderPage()
    expect(screen.getByText('WebSocket connection failed.')).toBeInTheDocument()
  })

  it('does not show WS banner on initial load when not yet connected', () => {
    hookReturn = { ...defaultReturn, wsConnected: false, wsSetupError: null, selectedIds: new Set() }
    renderPage()
    expect(screen.queryByText(/real-time updates disconnected/i)).not.toBeInTheDocument()
    expect(screen.queryByText('WebSocket connection failed.')).not.toBeInTheDocument()
  })

  it('renders empty state when no approvals', () => {
    renderPage()
    expect(screen.getByText('No approvals')).toBeInTheDocument()
  })

  it('renders metric cards with pending counts per risk level', () => {
    hookReturn = {
      ...defaultReturn,
      approvals: [
        makeApproval('1', { risk_level: 'critical', status: 'pending' }),
        makeApproval('2', { risk_level: 'critical', status: 'pending' }),
        makeApproval('3', { risk_level: 'high', status: 'pending' }),
      ],
      selectedIds: new Set(),
    }
    renderPage()
    // MetricCard values via stable test ID; order: Critical, High, Medium, Low
    const metricValues = screen.getAllByTestId('metric-value')
    const values = metricValues.map((el) => el.textContent)
    expect(values).toEqual(['2', '1', '0', '0'])
  })

  it('renders approval cards grouped by risk level', () => {
    hookReturn = {
      ...defaultReturn,
      approvals: [
        makeApproval('1', { risk_level: 'critical', title: 'Deploy prod' }),
        makeApproval('2', { risk_level: 'high', title: 'Push to main' }),
      ],
      selectedIds: new Set(),
    }
    renderPage()
    // Scope card assertions to each risk group via stable test ID
    expect(within(screen.getByTestId('riskgroup-critical')).getByText('Deploy prod')).toBeInTheDocument()
    expect(within(screen.getByTestId('riskgroup-high')).getByText('Push to main')).toBeInTheDocument()
  })

  it('does not render skeleton when loading with existing data', () => {
    hookReturn = {
      ...defaultReturn,
      loading: true,
      approvals: [makeApproval('1')],
      selectedIds: new Set(),
    }
    renderPage()
    expect(screen.queryByLabelText('Loading approvals')).not.toBeInTheDocument()
  })
})
