import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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

  it('opens on the queue rather than the archive', async () => {
    // 58 settled rows headed "0 pending", under risk buckets all reading
    // zero, is the page an operator opens to decide things.
    hookReturn = {
      ...defaultReturn,
      approvals: [
        makeApproval('1', { status: 'pending', title: 'Needs you' }),
        makeApproval('2', { status: 'approved', title: 'Already decided' }),
        makeApproval('3', { status: 'rejected', title: 'Already refused' }),
      ],
      selectedIds: new Set(),
    }
    renderPage()
    expect(screen.getByText('Needs you')).toBeInTheDocument()
    expect(screen.queryByText('Already decided')).not.toBeInTheDocument()
    expect(screen.queryByText('Already refused')).not.toBeInTheDocument()

    // And the archive is still one explicit choice away.
    await userEvent.selectOptions(
      screen.getByLabelText('Filter by status'),
      'all',
    )
    await waitFor(() => {
      expect(screen.getByText('Already decided')).toBeInTheDocument()
    })
    // Both archived statuses: asserting only the approved one still passes on
    // a regression that drops rejected approvals from the archive entirely.
    expect(screen.getByText('Already refused')).toBeInTheDocument()
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

  describe('per-card reject', () => {
    function cardRejectButton(title: string) {
      // ApprovalCard's accessible name is `Approval: <title>` (aria-label).
      return within(
        screen.getByRole('article', { name: `Approval: ${title}` }),
      ).getByRole('button', { name: /reject/i })
    }

    it('rejects directly from the card via the reason dialog (no drawer detour)', async () => {
      const user = userEvent.setup()
      hookReturn = {
        ...defaultReturn,
        approvals: [makeApproval('a1', { risk_level: 'high', status: 'pending', title: 'Ship it' })],
        rejectOne: vi.fn().mockResolvedValue(makeApproval('a1', { status: 'rejected' })),
        selectedIds: new Set(),
      }
      renderPage()

      // The per-card Reject opens the reject-reason dialog directly, not the
      // detail drawer (role="dialog"): the reason field is reachable in one click.
      await user.click(cardRejectButton('Ship it'))
      const dialog = await screen.findByRole('alertdialog')
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

      await user.type(
        within(dialog).getByLabelText(/reason for rejection/i),
        'Not aligned with the brief',
      )
      await user.click(within(dialog).getByRole('button', { name: /^reject$/i }))

      expect(hookReturn.rejectOne).toHaveBeenCalledOnce()
      expect(hookReturn.rejectOne).toHaveBeenCalledWith('a1', {
        reason: 'Not aligned with the brief',
      })
      // The dialog closes once the reject resolves.
      await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    })

    it('does not reject when the reason is blank', async () => {
      const user = userEvent.setup()
      hookReturn = {
        ...defaultReturn,
        approvals: [makeApproval('a1', { risk_level: 'high', status: 'pending', title: 'Ship it' })],
        rejectOne: vi.fn().mockResolvedValue(makeApproval('a1', { status: 'rejected' })),
        selectedIds: new Set(),
      }
      renderPage()

      await user.click(cardRejectButton('Ship it'))
      const dialog = await screen.findByRole('alertdialog')
      await user.click(within(dialog).getByRole('button', { name: /^reject$/i }))

      expect(hookReturn.rejectOne).not.toHaveBeenCalled()
      // Validation keeps the dialog open so the operator can supply a reason.
      expect(screen.getByRole('alertdialog')).toBeInTheDocument()
    })

    it('reopens the reject dialog for the same card after cancel', async () => {
      const user = userEvent.setup()
      hookReturn = {
        ...defaultReturn,
        approvals: [makeApproval('a1', { risk_level: 'high', status: 'pending', title: 'Ship it' })],
        selectedIds: new Set(),
      }
      renderPage()

      await user.click(cardRejectButton('Ship it'))
      expect(await screen.findByRole('alertdialog')).toBeInTheDocument()
      await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: /cancel/i }))
      await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())

      // Cancelling cleared the target, so the same card can be reopened.
      await user.click(cardRejectButton('Ship it'))
      expect(await screen.findByRole('alertdialog')).toBeInTheDocument()
      expect(hookReturn.rejectOne).not.toHaveBeenCalled()
    })

    it('targets the correct card when several are pending', async () => {
      const user = userEvent.setup()
      hookReturn = {
        ...defaultReturn,
        approvals: [
          makeApproval('a1', { risk_level: 'high', status: 'pending', title: 'Ship it' }),
          makeApproval('b2', { risk_level: 'high', status: 'pending', title: 'Deploy widget' }),
        ],
        rejectOne: vi.fn().mockResolvedValue(makeApproval('b2', { status: 'rejected' })),
        selectedIds: new Set(),
      }
      renderPage()

      await user.click(cardRejectButton('Deploy widget'))
      const dialog = await screen.findByRole('alertdialog')
      await user.type(within(dialog).getByLabelText(/reason for rejection/i), 'Wrong target')
      await user.click(within(dialog).getByRole('button', { name: /^reject$/i }))

      expect(hookReturn.rejectOne).toHaveBeenCalledOnce()
      expect(hookReturn.rejectOne).toHaveBeenCalledWith('b2', { reason: 'Wrong target' })
    })
  })
})
