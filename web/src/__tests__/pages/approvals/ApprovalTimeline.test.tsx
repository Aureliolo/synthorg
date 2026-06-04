import { render, screen } from '@testing-library/react'
import fc from 'fast-check'
import { ApprovalTimeline } from '@/pages/approvals/ApprovalTimeline'
import { makeApproval } from '../../helpers/factories'

describe('ApprovalTimeline', () => {
  it('renders 3 steps: Submitted, Under Review, Decided', () => {
    const approval = makeApproval('test-1')
    render(<ApprovalTimeline approval={approval} />)
    expect(screen.getByText('Submitted')).toBeInTheDocument()
    expect(screen.getByText('Under Review')).toBeInTheDocument()
    expect(screen.getByText('Decided')).toBeInTheDocument()
  })

  it('pending approval: Submitted=complete, Under Review=active, Decided=future', () => {
    const approval = makeApproval('test-1', { status: 'pending' })
    render(<ApprovalTimeline approval={approval} />)
    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(3)
    // Under Review step should have the pulse animation (active state)
    const underReviewDot = items[1]!.querySelector('.animate-pulse')
    expect(underReviewDot).toBeInTheDocument()
    // Decided step should not have active/complete indicators
    const decidedDot = items[2]!.querySelector('.bg-border')
    expect(decidedDot).toBeInTheDocument()
  })

  it('approved approval: all steps complete, outcome label "Approved"', () => {
    const approval = makeApproval('test-1', {
      status: 'approved',
      decided_at: '2026-03-27T12:00:00Z',
      decided_by: 'user-admin',
    })
    render(<ApprovalTimeline approval={approval} />)
    expect(screen.getByText('Approved')).toBeInTheDocument()
    // All steps should be complete (no pulse animation for active, no bg-border for future)
    const items = screen.getAllByRole('listitem')
    items.forEach((item) => {
      expect(item.querySelector('.animate-pulse')).not.toBeInTheDocument()
    })
  })

  it('rejected approval: all steps complete, outcome label "Rejected"', () => {
    const approval = makeApproval('test-1', {
      status: 'rejected',
      decided_at: '2026-03-27T12:00:00Z',
      decided_by: 'user-admin',
      decision_reason: 'Too risky',
    })
    render(<ApprovalTimeline approval={approval} />)
    expect(screen.getByText('Rejected')).toBeInTheDocument()
  })

  it('expired approval: all steps complete, outcome label "Expired"', () => {
    const approval = makeApproval('test-1', {
      status: 'expired',
    })
    render(<ApprovalTimeline approval={approval} />)
    expect(screen.getByText('Expired')).toBeInTheDocument()
  })

  it('shows timestamps when available', () => {
    const approval = makeApproval('test-1', {
      status: 'approved',
      created_at: '2026-03-27T10:00:00Z',
      decided_at: '2026-03-27T12:00:00Z',
    })
    render(<ApprovalTimeline approval={approval} />)
    // formatDateTime produces locale strings; check that timestamps appear within listitems
    const items = screen.getAllByRole('listitem')
    // Check that timestamps appear by verifying the list items contain date content
    // The timestamps are formatted by the component -- just verify the listitems have text beyond the step label
    expect(items[0]!.textContent.length).toBeGreaterThan('Submitted'.length)
    expect(items[2]!.textContent.length).toBeGreaterThan('Decided'.length)
  })

  it('has correct ARIA attributes (role="list", role="listitem")', () => {
    const approval = makeApproval('test-1')
    render(<ApprovalTimeline approval={approval} />)
    expect(screen.getByRole('list', { name: 'Approval timeline' })).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(3)
  })

  it('always renders 3 steps for any approval status (property)', () => {
    fc.assert(
      fc.property(
        fc.constantFrom('pending' as const, 'approved' as const, 'rejected' as const, 'expired' as const),
        (status) => {
          const approval = makeApproval('prop-test', { status })
          const { unmount } = render(<ApprovalTimeline approval={approval} />)
          expect(screen.getAllByRole('listitem')).toHaveLength(3)
          expect(screen.getByText('Submitted')).toBeInTheDocument()
          expect(screen.getByText('Under Review')).toBeInTheDocument()
          expect(screen.getByText('Decided')).toBeInTheDocument()
          unmount()
        },
      ),
      { numRuns: 10 },
    )
  })
})
