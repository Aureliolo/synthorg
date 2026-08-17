import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ApprovalFilterBar } from '@/pages/approvals/ApprovalFilterBar'
import type { ApprovalPageFilters } from '@/utils/approvals'

const defaultProps = {
  filters: {} as ApprovalPageFilters,
  onFiltersChange: vi.fn(),
  pendingCount: 5,
  totalCount: 12,
  actionTypes: ['deploy:production', 'code:create', 'budget:increase'],
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('ApprovalFilterBar', () => {
  it('renders all filter controls (status, risk level, action type, search input)', () => {
    render(<ApprovalFilterBar {...defaultProps} />)
    expect(screen.getByLabelText('Filter by status')).toBeInTheDocument()
    expect(screen.getByLabelText('Filter by risk level')).toBeInTheDocument()
    expect(screen.getByLabelText('Filter by action type')).toBeInTheDocument()
    expect(screen.getByLabelText('Search approvals')).toBeInTheDocument()
  })

  it('changing status select calls onFiltersChange with correct filter', async () => {
    const user = userEvent.setup()
    render(<ApprovalFilterBar {...defaultProps} />)
    await user.selectOptions(screen.getByLabelText('Filter by status'), 'approved')
    expect(defaultProps.onFiltersChange).toHaveBeenCalledWith({ status: 'approved' })
  })

  it('changing risk level calls onFiltersChange', async () => {
    const user = userEvent.setup()
    render(<ApprovalFilterBar {...defaultProps} />)
    await user.selectOptions(screen.getByLabelText('Filter by risk level'), 'critical')
    expect(defaultProps.onFiltersChange).toHaveBeenCalledWith({ riskLevel: 'critical' })
  })

  it('typing in search input calls onFiltersChange', async () => {
    const user = userEvent.setup()
    render(<ApprovalFilterBar {...defaultProps} />)
    await user.type(screen.getByLabelText('Search approvals'), 'x')
    // The component calls onFiltersChange on each keystroke with the current input value
    expect(defaultProps.onFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ search: 'x' }),
    )
  })

  it('active filter pills are displayed for each active filter', () => {
    render(
      <ApprovalFilterBar
        {...defaultProps}
        filters={{
          status: 'approved',
          riskLevel: 'high',
          actionType: 'deploy:production',
          search: 'api',
        }}
      />,
    )
    expect(screen.getByText('Status: Approved')).toBeInTheDocument()
    expect(screen.getByText('Risk: High')).toBeInTheDocument()
    expect(screen.getByText('Type: deploy:production')).toBeInTheDocument()
    expect(screen.getByText('Search: "api"')).toBeInTheDocument()
  })

  it('clicking X on a filter pill removes that filter', async () => {
    const user = userEvent.setup()
    render(
      <ApprovalFilterBar
        {...defaultProps}
        filters={{ status: 'approved', riskLevel: 'high' }}
      />,
    )
    await user.click(screen.getByLabelText('Remove filter: Status: Approved'))
    expect(defaultProps.onFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ status: undefined, riskLevel: 'high' }),
    )
  })

  it('"Clear all" button calls onFiltersChange({})', async () => {
    const user = userEvent.setup()
    render(
      <ApprovalFilterBar
        {...defaultProps}
        filters={{ status: 'approved' }}
      />,
    )
    await user.click(screen.getByText('Clear all'))
    expect(defaultProps.onFiltersChange).toHaveBeenCalledWith({})
  })

  it('displays pending/total counts', () => {
    render(<ApprovalFilterBar {...defaultProps} pendingCount={3} totalCount={15} />)
    expect(screen.getByText('3 pending / 15 total')).toBeInTheDocument()
  })

  it('does not render filter pills when no filters are active', () => {
    render(<ApprovalFilterBar {...defaultProps} />)
    expect(screen.queryByText('Clear all')).not.toBeInTheDocument()
  })

  it('shows no pill for the queue the page opens on', () => {
    // The default is not a choice the operator made, so offering to remove it
    // would offer to return to where they already are.
    render(<ApprovalFilterBar {...defaultProps} filters={{ status: 'pending' }} />)
    expect(screen.queryByText('Status: Pending')).not.toBeInTheDocument()
    expect(screen.queryByText('Clear all')).not.toBeInTheDocument()
  })

  it('offers the archive as a named choice rather than as an empty value', () => {
    // The empty value means "unset", and unset resolves to the queue; the
    // archive has to be something the operator can pick and stay on.
    render(<ApprovalFilterBar {...defaultProps} filters={{ status: 'all' }} />)
    const select = screen.getByLabelText<HTMLSelectElement>('Filter by status')
    expect(select.value).toBe('all')
    expect(screen.getByText('Status: All statuses')).toBeInTheDocument()
  })
})
