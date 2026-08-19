import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import fc from 'fast-check'
import { ApprovalCard } from '@/pages/approvals/ApprovalCard'
import { makeApproval } from '../../helpers/factories'

const defaultHandlers = {
  onSelect: vi.fn(),
  onApprove: vi.fn(),
  onReject: vi.fn(),
  onToggleSelect: vi.fn(),
}

function renderCard(overrides: Parameters<typeof makeApproval>[1] = {}, selected = false) {
  const approval = makeApproval('test-1', {
    title: 'Deploy API',
    action_type: 'deploy:production',
    requested_by: 'agent-eng',
    risk_level: 'critical',
    seconds_remaining: 3600,
    urgency_level: 'critical',
    ...overrides,
  })
  return render(
    <ApprovalCard
      approval={approval}
      selected={selected}
      {...defaultHandlers}
    />,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('ApprovalCard', () => {
  it('renders title and a human step label (not the raw action type)', () => {
    renderCard()
    expect(screen.getByText('Deploy API')).toBeInTheDocument()
    // Only the human step label is rendered; the raw action_type string is not.
    expect(screen.queryByText('deploy:production')).not.toBeInTheDocument()
    // A production deploy is not a review of finished work, whatever gate
    // parked it, so the label states the decision rather than borrowing the
    // review gate's wording.
    expect(screen.getByText('Approve to continue')).toBeInTheDocument()
  })

  it('calls a completed review a completed review', () => {
    renderCard({ action_type: 'review:task_completion' })
    expect(screen.getByText('Review completed work')).toBeInTheDocument()
  })

  it('badges a failed run even when no run context resolved', () => {
    // The badge is the danger signal on the card. Reading only the optional
    // run enrichment dropped it from a row that had failed, so that card was
    // the one thing in the queue not marked as a failure.
    renderCard({ action_type: 'review:task_failed', run: null })
    expect(screen.getByText('Run failed')).toBeInTheDocument()
  })

  it('says the agent is unknown rather than printing the requester', () => {
    renderCard()
    expect(screen.getByText('Unknown agent')).toBeInTheDocument()
    expect(screen.queryByText('agent-eng')).not.toBeInTheDocument()
  })

  it('never prints the key when the resolved ref carries no name', () => {
    // A retired agent: the id is on the ref for linking, and the card must
    // not fall back to it. The old fallback put a UUID on the surface.
    renderCard({ agent: { id: '2019c07a-8bd0-4a1f-9f4e-11d2b5b7a001', name: null } })
    expect(screen.getByText('Unknown agent')).toBeInTheDocument()
    expect(
      screen.queryByText('2019c07a-8bd0-4a1f-9f4e-11d2b5b7a001'),
    ).not.toBeInTheDocument()
  })

  it('shows resolved task title, project name, and agent name (no UUIDs)', () => {
    renderCard({
      title: 'Review: onboarding',
      task: { id: '88c5f343-e47e-42bc-a55d-0782aab2e38b', title: 'Ship onboarding', status: 'in_review' },
      project: { id: '6f6c5c0a-5f9d-4d2d-9c0c-2f8c5b7f4b12', name: 'Platform' },
      agent: { id: '2019c07a-8bd0', name: 'Anica Hocevar' },
    })
    expect(screen.getByText('Ship onboarding')).toBeInTheDocument()
    expect(screen.getByText('Platform')).toBeInTheDocument()
    expect(screen.getByText('Anica Hocevar')).toBeInTheDocument()
    expect(screen.queryByText('88c5f343-e47e-42bc-a55d-0782aab2e38b')).not.toBeInTheDocument()
    expect(
      screen.queryByText('6f6c5c0a-5f9d-4d2d-9c0c-2f8c5b7f4b12'),
    ).not.toBeInTheDocument()
    expect(screen.queryByText('2019c07a-8bd0')).not.toBeInTheDocument()
  })

  it('renders a run-outcome badge when a run summary is present', () => {
    renderCard({ run: { outcome: 'succeeded', produced_artifact_count: 2, artifacts: [] } })
    expect(screen.getByLabelText('Run outcome: Produced output')).toBeInTheDocument()
  })

  it('marks a failed run distinctly: danger badge + Acknowledge/Retry buttons', () => {
    renderCard({
      action_type: 'review:task_failed',
      run: { outcome: 'failed', produced_artifact_count: 0, artifacts: [] },
    })
    expect(screen.getByLabelText('Run outcome: Run failed')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /acknowledge/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^approve$/i })).not.toBeInTheDocument()
  })

  it('renders urgency countdown for pending items', () => {
    renderCard({ seconds_remaining: 7200 })
    expect(screen.getByText('2h 0m')).toBeInTheDocument()
  })

  it('renders "No expiry" when no TTL', () => {
    renderCard({ seconds_remaining: null, urgency_level: 'no_expiry' })
    expect(screen.getByText('No expiry')).toBeInTheDocument()
  })

  it('shows approve/reject buttons for pending items', () => {
    renderCard()
    expect(screen.getByRole('button', { name: /approve/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /reject/i })).toBeInTheDocument()
  })

  it('hides approve/reject buttons for non-pending items', () => {
    renderCard({ status: 'approved' })
    expect(screen.queryByRole('button', { name: /approve/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /reject/i })).not.toBeInTheDocument()
  })

  it('shows checkbox for pending items', () => {
    renderCard()
    expect(screen.getByRole('checkbox')).toBeInTheDocument()
  })

  it('hides checkbox for non-pending items', () => {
    renderCard({ status: 'rejected' })
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })

  it('calls onSelect when title is clicked', async () => {
    renderCard()
    await userEvent.click(screen.getByText('Deploy API'))
    expect(defaultHandlers.onSelect).toHaveBeenCalledWith('test-1')
  })

  it('calls onApprove when approve button is clicked', async () => {
    renderCard()
    await userEvent.click(screen.getByRole('button', { name: /approve/i }))
    expect(defaultHandlers.onApprove).toHaveBeenCalledWith('test-1')
  })

  it('calls onReject when reject button is clicked', async () => {
    renderCard()
    await userEvent.click(screen.getByRole('button', { name: /reject/i }))
    expect(defaultHandlers.onReject).toHaveBeenCalledWith('test-1')
  })

  it('routes a decision fork to the drawer instead of a one-click approve', async () => {
    // A decision fork needs a chosen option, so the card offers "Review to
    // choose" (opens the drawer) rather than the approve/reject pair.
    renderCard({
      evidence_package: {
        id: 'ev-dec',
        title: 'Fork',
        narrative: 'Pick one',
        reasoning_trace: [],
        recommended_actions: [],
        metadata: {},
        signature_threshold: 1,
        signatures: [],
        is_fully_signed: false,
        source_agent_id: 'agent-eng',
        task_id: null,
        risk_level: 'medium',
        created_at: '2026-05-19T12:00:00Z',
        chosen_option_id: null,
        options: [
          { id: 'a', title: 'A', summary: 'first', recommended: true },
          { id: 'b', title: 'B', summary: 'second', recommended: false },
        ],
      },
    })
    expect(screen.queryByRole('button', { name: /^approve$/i })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /review to choose/i }))
    expect(defaultHandlers.onSelect).toHaveBeenCalledWith('test-1')
    expect(defaultHandlers.onApprove).not.toHaveBeenCalled()
  })

  it('calls onToggleSelect when checkbox is clicked', async () => {
    renderCard()
    await userEvent.click(screen.getByRole('checkbox'))
    expect(defaultHandlers.onToggleSelect).toHaveBeenCalledWith('test-1')
  })

  it('marks checkbox as checked when selected', () => {
    renderCard({}, true)
    expect(screen.getByRole('checkbox')).toBeChecked()
  })

  it('renders without crashing for any status/countdown/urgency (property)', () => {
    fc.assert(
      fc.property(
        fc.constantFrom('pending' as const, 'approved' as const, 'rejected' as const, 'expired' as const),
        fc.option(fc.integer({ min: 0, max: 86400 }), { nil: null }),
        fc.constantFrom('critical' as const, 'high' as const, 'normal' as const, 'no_expiry' as const),
        (status, secondsRemaining, urgencyLevel) => {
          const { unmount } = renderCard({
            status,
            seconds_remaining: secondsRemaining,
            urgency_level: urgencyLevel,
          })
          if (status === 'pending') {
            expect(screen.getByRole('button', { name: /approve/i })).toBeInTheDocument()
          } else {
            expect(screen.queryByRole('button', { name: /approve/i })).not.toBeInTheDocument()
          }
          unmount()
        },
      ),
      { numRuns: 20 },
    )
  })
})
