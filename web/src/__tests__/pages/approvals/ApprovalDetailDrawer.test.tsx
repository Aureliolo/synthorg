import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { ApprovalDetailDrawer } from '@/pages/approvals/ApprovalDetailDrawer'
import { makeApproval } from '../../helpers/factories'
import { useToastStore } from '@/stores/toast'

// Mock components must be at module scope for eslint @eslint-react/component-hook-factories
function MockAnimatePresence({ children }: { children: React.ReactNode }) {
  return <>{children}</>
}

function MockDiv(props: React.ComponentProps<'div'> & Record<string, unknown>) {
  return (
    <div className={props.className} onClick={props.onClick}>
      {props.children}
    </div>
  )
}

function MockAside(props: React.ComponentProps<'aside'> & Record<string, unknown>) {
  return (
    <aside
      className={props.className}
      role={props.role}
      aria-modal={props['aria-modal']}
      aria-label={props['aria-label']}
      ref={props.ref}
    >
      {props.children}
    </aside>
  )
}

// Mock motion/react to avoid animation timing issues in tests
vi.mock('motion/react', async () => {
  const actual = await vi.importActual<typeof import('motion/react')>('motion/react')
  return {
    ...actual,
    AnimatePresence: MockAnimatePresence,
    motion: Object.assign({}, actual.motion, {
      div: MockDiv,
      aside: MockAside,
    }),
  }
})

// Factory that builds fresh mock handlers per test. Using a factory instead of
// a module-level constant prevents mockRejectedValueOnce queues from leaking
// across tests when an earlier test fails before consuming the queued rejection.
function makeHandlers() {
  return {
    onClose: vi.fn(),
    onApprove: vi.fn<(
      id: string,
      data?: {
        readonly comment?: string | null
        readonly chosen_option_id?: string | null
      },
    ) => Promise<boolean>>()
      .mockResolvedValue(true),
    onReject: vi.fn<(id: string, data: { readonly reason: string }) => Promise<boolean>>()
      .mockResolvedValue(true),
  }
}

type Handlers = ReturnType<typeof makeHandlers>
let defaultHandlers: Handlers

function renderDrawer(
  overrides: Parameters<typeof makeApproval>[1] = {},
  props: Partial<React.ComponentProps<typeof ApprovalDetailDrawer>> = {},
) {
  const approval = makeApproval('test-1', {
    title: 'Deploy to production',
    description: 'Deploy API v2 to production cluster',
    action_type: 'deploy:production',
    requested_by: 'agent-eng',
    risk_level: 'critical',
    ...overrides,
  })
  return render(
    <MemoryRouter>
      <ApprovalDetailDrawer
        approval={approval}
        open={true}
        {...defaultHandlers}
        {...props}
      />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.resetAllMocks()
  defaultHandlers = makeHandlers()
  useToastStore.setState({ toasts: [] })
})

describe('ApprovalDetailDrawer', () => {
  it('renders with approval data (title, description, risk level badge, status label)', () => {
    renderDrawer()
    expect(screen.getByText('Deploy to production')).toBeInTheDocument()
    expect(screen.getByText('Deploy API v2 to production cluster')).toBeInTheDocument()
    // "Critical" appears in both the risk badge and the metadata grid
    expect(screen.getAllByText('Critical').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('Pending')).toBeInTheDocument()
  })

  it('shows loading spinner when loading is true and approval is null', () => {
    render(
      <ApprovalDetailDrawer
        approval={null}
        open={true}
        loading={true}
        {...defaultHandlers}
      />,
    )
    expect(screen.getByRole('status', { name: 'Loading approval' })).toBeInTheDocument()
  })

  it('shows error state when error prop is provided', () => {
    render(
      <ApprovalDetailDrawer
        approval={null}
        open={true}
        error="Failed to load approval"
        {...defaultHandlers}
      />,
    )
    expect(screen.getByText('Failed to load approval')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /close/i })).toBeInTheDocument()
  })

  it('error state hides stale approval content', () => {
    render(
      <ApprovalDetailDrawer
        approval={makeApproval('stale-1', { title: 'Stale approval title' })}
        open={true}
        error="Refetch failed"
        {...defaultHandlers}
      />,
    )
    expect(screen.getByText('Refetch failed')).toBeInTheDocument()
    expect(screen.queryByText('Stale approval title')).not.toBeInTheDocument()
  })

  it('close button calls onClose', async () => {
    const user = userEvent.setup()
    renderDrawer()
    await user.click(screen.getByRole('button', { name: /^close$/i }))
    expect(defaultHandlers.onClose).toHaveBeenCalledOnce()
  })

  it('Escape key calls onClose when no confirm dialog is open', async () => {
    const user = userEvent.setup()
    renderDrawer()
    await user.keyboard('{Escape}')
    expect(defaultHandlers.onClose).toHaveBeenCalledOnce()
  })

  it('approve button opens confirm dialog', async () => {
    const user = userEvent.setup()
    renderDrawer()
    await user.click(screen.getByRole('button', { name: /approve/i }))
    expect(screen.getByText('Approve Action')).toBeInTheDocument()
    expect(screen.getByText('Are you sure you want to approve this action?')).toBeInTheDocument()
  })

  it('reject button opens confirm dialog', async () => {
    const user = userEvent.setup()
    renderDrawer()
    await user.click(screen.getByRole('button', { name: /reject/i }))
    expect(screen.getByText('Reject Action')).toBeInTheDocument()
    expect(screen.getByText('Please provide a reason for rejection.')).toBeInTheDocument()
  })

  it('relabels the approve dialog to Acknowledge for a failed run', async () => {
    const user = userEvent.setup()
    renderDrawer({ action_type: 'review:task_failed' })
    await user.click(screen.getByRole('button', { name: /acknowledge/i }))
    expect(screen.getByText('Acknowledge failure')).toBeInTheDocument()
  })

  it('relabels the reject dialog to Retry for a failed run', async () => {
    const user = userEvent.setup()
    renderDrawer({ action_type: 'review:task_failed' })
    await user.click(screen.getByRole('button', { name: /retry/i }))
    expect(screen.getByText('Retry task')).toBeInTheDocument()
  })

  it('reject requires non-empty reason (shows toast error)', async () => {
    const user = userEvent.setup()
    renderDrawer()
    // Open reject dialog (drawer button).
    await user.click(screen.getByRole('button', { name: /reject/i }))
    // Scope the confirm button lookup to the alertdialog so the query cannot
    // ambiguously match the drawer's own Reject button.
    const rejectDialog = screen.getByRole('alertdialog')
    await user.click(within(rejectDialog).getByRole('button', { name: /reject/i }))
    // Should show toast error -- onReject should NOT have been called
    expect(defaultHandlers.onReject).not.toHaveBeenCalled()
    const toasts = useToastStore.getState().toasts
    expect(toasts.some((t) => t.title === 'Rejection reason required')).toBe(true)
  })

  it('renders conditional fields for decided approvals', () => {
    renderDrawer({
      status: 'approved',
      expires_at: '2026-04-01T00:00:00Z',
      seconds_remaining: 7200,
      decided_by: 'admin-user',
      decided_at: '2026-03-27T15:00:00Z',
      decision_reason: 'All checks passed',
      task_id: 'task-42',
      // The drawer's metadata renderer falls through to JSON.stringify
      // for non-string values; the type forbids non-string values, so we
      // deliberately violate it to exercise that branch.
      // @ts-expect-error -- intentional: exercise non-string metadata branch
      metadata: { region: 'eu-west', nested: { deep: true } },
    })
    expect(screen.getByText('Decided By')).toBeInTheDocument()
    expect(screen.getByText('admin-user')).toBeInTheDocument()
    expect(screen.getByText('Decided At')).toBeInTheDocument()
    expect(screen.getByText('Expires')).toBeInTheDocument()
    expect(screen.getByText('All checks passed')).toBeInTheDocument()
    expect(screen.getByText('task-42')).toBeInTheDocument()
    expect(screen.getByText('eu-west')).toBeInTheDocument()
    expect(screen.getByText('{"deep":true}')).toBeInTheDocument()
  })

  it('keeps the approve dialog open and preserves the comment when onApprove returns false', async () => {
    const user = userEvent.setup()
    defaultHandlers.onApprove.mockResolvedValueOnce(false)
    renderDrawer()
    await user.click(screen.getByRole('button', { name: /approve/i }))
    await user.type(screen.getByLabelText('Optional comment'), 'Looks good')
    const approveDialog = screen.getByRole('alertdialog')
    await user.click(within(approveDialog).getByRole('button', { name: /approve/i }))
    // Wait for the async onApprove to resolve before asserting the
    // dialog is still mounted -- otherwise we could be reading DOM
    // state from before the handler had a chance to return false.
    await waitFor(() => {
      expect(defaultHandlers.onApprove).toHaveBeenCalledOnce()
    })
    // Drawer must NOT close the dialog; the underlying store has already
    // surfaced the error toast.
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()
    expect(
      screen.getByLabelText<HTMLTextAreaElement>('Optional comment').value,
    ).toBe('Looks good')
  })

  it('keeps the reject dialog open and preserves the reason when onReject returns false', async () => {
    const user = userEvent.setup()
    defaultHandlers.onReject.mockResolvedValueOnce(false)
    renderDrawer()
    await user.click(screen.getByRole('button', { name: /reject/i }))
    await user.type(screen.getByLabelText(/reason for rejection/i), 'Missing context')
    const rejectDialog = screen.getByRole('alertdialog')
    await user.click(within(rejectDialog).getByRole('button', { name: /reject/i }))
    await waitFor(() => {
      expect(defaultHandlers.onReject).toHaveBeenCalledOnce()
    })
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()
    expect(
      screen.getByLabelText<HTMLTextAreaElement>(/reason for rejection/i).value,
    ).toBe('Missing context')
  })

  it('successful approve submits with comment', async () => {
    const user = userEvent.setup()
    renderDrawer()
    await user.click(screen.getByRole('button', { name: /approve/i }))
    await user.type(screen.getByLabelText('Optional comment'), 'Looks good')
    const approveDialog = screen.getByRole('alertdialog')
    await user.click(within(approveDialog).getByRole('button', { name: /approve/i }))
    expect(defaultHandlers.onApprove).toHaveBeenCalledWith('test-1', { comment: 'Looks good' })
  })

  it('successful reject submits with reason', async () => {
    const user = userEvent.setup()
    renderDrawer()
    await user.click(screen.getByRole('button', { name: /reject/i }))
    await user.type(screen.getByLabelText(/reason for rejection/i), 'Missing documentation')
    const rejectDialog = screen.getByRole('alertdialog')
    await user.click(within(rejectDialog).getByRole('button', { name: /reject/i }))
    expect(defaultHandlers.onReject).toHaveBeenCalledWith('test-1', { reason: 'Missing documentation' })
  })

  it('Escape does not close drawer when confirm dialog is open', async () => {
    const user = userEvent.setup()
    renderDrawer()
    // Open approve confirm dialog
    await user.click(screen.getByRole('button', { name: /approve/i }))
    expect(screen.getByText('Approve Action')).toBeInTheDocument()
    // Escape closes the Base UI AlertDialog but should NOT close the drawer
    await user.keyboard('{Escape}')
    expect(defaultHandlers.onClose).not.toHaveBeenCalled()
    // Drawer itself is still mounted
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('auto-closes an open confirm dialog when the approval becomes decided', async () => {
    // A live WS upsert can flip the approval to decided while the operator has
    // the approve dialog open; the dialog must close so a stale action cannot
    // be confirmed against an already-decided approval.
    const user = userEvent.setup()
    const { rerender } = render(
      <MemoryRouter>
        <ApprovalDetailDrawer
          approval={makeApproval('test-1', { status: 'pending', risk_level: 'critical' })}
          open={true}
          {...defaultHandlers}
        />
      </MemoryRouter>,
    )
    await user.click(screen.getByRole('button', { name: /approve/i }))
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()

    rerender(
      <MemoryRouter>
        <ApprovalDetailDrawer
          approval={makeApproval('test-1', { status: 'approved', risk_level: 'critical' })}
          open={true}
          {...defaultHandlers}
        />
      </MemoryRouter>,
    )
    await waitFor(() => {
      expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    })
  })

  it('renders a modal dialog surface with a dismiss backdrop', () => {
    // Modality (focus trap, scroll lock, outside-dismiss) is the shared
    // Base UI Drawer's responsibility and is tested upstream. Here we assert
    // the application-level contract: a dialog surface plus the modal backdrop
    // the primitive renders.
    renderDrawer()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByTestId('drawer-overlay')).toBeInTheDocument()
  })

  it('renders nothing when open is false', () => {
    render(
      <ApprovalDetailDrawer
        approval={null}
        open={false}
        {...defaultHandlers}
      />,
    )
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('hides approve/reject buttons for non-pending approvals', () => {
    renderDrawer({ status: 'approved' })
    expect(screen.queryByRole('button', { name: /approve/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /reject/i })).not.toBeInTheDocument()
  })

  it('renders a confidence label derived from the confidence_score metadata', () => {
    renderDrawer({ metadata: { confidence_score: '0.42' } })
    expect(screen.getByText('Confidence')).toBeInTheDocument()
    expect(screen.getByText('42%')).toBeInTheDocument()
  })

  it('omits the confidence field when no confidence_score metadata is present', () => {
    renderDrawer({ metadata: {} })
    expect(screen.queryByText('Confidence')).not.toBeInTheDocument()
  })

  it('shows the produced artifacts with type + size', () => {
    renderDrawer({
      run: {
        outcome: 'succeeded',
        produced_artifact_count: 1,
        artifacts: [
          {
            id: 'artifact-1',
            path: 'src/auth/oauth.ts',
            type: 'code',
            content_type: 'text/x-typescript',
            size_bytes: 4096,
          },
        ],
      },
    })
    expect(screen.getByText('What was produced')).toBeInTheDocument()
    expect(screen.getByText('src/auth/oauth.ts')).toBeInTheDocument()
  })

  it('shows an explicit empty state when the run produced nothing', () => {
    renderDrawer({
      run: { outcome: 'empty', produced_artifact_count: 0, artifacts: [] },
    })
    expect(screen.getByText('No artifacts produced')).toBeInTheDocument()
  })

  it('shows a failure banner and Acknowledge/Retry for a failed run', () => {
    renderDrawer({
      action_type: 'review:task_failed',
      decision_reason: null,
      run: { outcome: 'failed', produced_artifact_count: 0, artifacts: [] },
    })
    expect(screen.getByText('This run failed')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /acknowledge/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })
})

/** An evidence package carrying a two-option execution-time decision fork. */
function decisionEvidence() {
  return {
    id: 'ev-dec',
    title: 'Core engine architecture',
    narrative: 'How should the core engine be structured?',
    reasoning_trace: [],
    recommended_actions: [],
    metadata: {},
    signature_threshold: 1,
    signatures: [],
    is_fully_signed: false,
    source_agent_id: 'agent-eng',
    task_id: null,
    risk_level: 'medium' as const,
    created_at: '2026-05-19T12:00:00Z',
    chosen_option_id: null,
    options: [
      {
        id: 'actor',
        title: 'Actor model',
        summary: 'Message-passing isolation; higher upfront complexity.',
        recommended: true,
      },
      {
        id: 'pipeline',
        title: 'Pipeline model',
        summary: 'Simple staged flow; weaker back-pressure control.',
        recommended: false,
      },
    ],
  }
}

describe('ApprovalDetailDrawer decision fork', () => {
  it('renders each option with its writeup and the recommendation', () => {
    renderDrawer({ evidence_package: decisionEvidence() })
    expect(screen.getByText('Choose an option')).toBeInTheDocument()
    expect(screen.getByText('Actor model')).toBeInTheDocument()
    expect(
      screen.getByText('Message-passing isolation; higher upfront complexity.'),
    ).toBeInTheDocument()
    expect(screen.getByText('Recommended')).toBeInTheDocument()
    // The recommended option is pre-selected.
    expect(screen.getByRole('radio', { name: /Actor model/ })).toHaveAttribute(
      'aria-checked',
      'true',
    )
  })

  it('approves with the recommended option by default', async () => {
    const user = userEvent.setup()
    renderDrawer({ evidence_package: decisionEvidence() })
    await user.click(screen.getByRole('button', { name: /^approve$/i }))
    const dialog = screen.getByRole('alertdialog')
    await user.click(within(dialog).getByRole('button', { name: /^approve$/i }))
    expect(defaultHandlers.onApprove).toHaveBeenCalledWith('test-1', {
      chosen_option_id: 'actor',
    })
  })

  it('approves with a different option once the operator selects it', async () => {
    const user = userEvent.setup()
    renderDrawer({ evidence_package: decisionEvidence() })
    await user.click(screen.getByRole('radio', { name: /Pipeline model/ }))
    await user.click(screen.getByRole('button', { name: /^approve$/i }))
    const dialog = screen.getByRole('alertdialog')
    await user.click(within(dialog).getByRole('button', { name: /^approve$/i }))
    expect(defaultHandlers.onApprove).toHaveBeenCalledWith('test-1', {
      chosen_option_id: 'pipeline',
    })
  })

  it('shows the operator-chosen option (not the recommended one) on a decided fork', () => {
    // A decided approval carries the actual pick on the evidence package; the
    // read-only option list must surface that, not fall back to recommended.
    renderDrawer({
      status: 'approved',
      evidence_package: { ...decisionEvidence(), chosen_option_id: 'pipeline' },
    })
    const pipelineRow = screen.getByText('Pipeline model').closest('li')
    if (!pipelineRow) throw new Error('pipeline option row not found')
    expect(within(pipelineRow).getByText('Selected')).toBeInTheDocument()
    const actorRow = screen.getByText('Actor model').closest('li')
    if (!actorRow) throw new Error('actor option row not found')
    expect(within(actorRow).queryByText('Selected')).not.toBeInTheDocument()
  })

  it('re-derives the shown pick when a WS update decides the displayed approval', async () => {
    // The id-keyed reset misses a decision on the SAME approval; a WS upsert
    // that records the operator's pick must move the shown selection off the
    // stale recommended default onto the persisted choice.
    const { rerender } = render(
      <MemoryRouter>
        <ApprovalDetailDrawer
          approval={makeApproval('test-1', {
            status: 'pending',
            evidence_package: decisionEvidence(),
          })}
          open={true}
          {...defaultHandlers}
        />
      </MemoryRouter>,
    )
    // Pending: the recommended option is the default selection.
    expect(screen.getByRole('radio', { name: /Actor model/ })).toHaveAttribute(
      'aria-checked',
      'true',
    )
    rerender(
      <MemoryRouter>
        <ApprovalDetailDrawer
          approval={makeApproval('test-1', {
            status: 'approved',
            evidence_package: { ...decisionEvidence(), chosen_option_id: 'pipeline' },
          })}
          open={true}
          {...defaultHandlers}
        />
      </MemoryRouter>,
    )
    await waitFor(() => {
      const pipelineRow = screen.getByText('Pipeline model').closest('li')
      if (!pipelineRow) throw new Error('pipeline option row not found')
      expect(within(pipelineRow).getByText('Selected')).toBeInTheDocument()
    })
  })
})
