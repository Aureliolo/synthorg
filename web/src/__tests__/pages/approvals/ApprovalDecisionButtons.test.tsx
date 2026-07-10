import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ApprovalDecisionButtons } from '@/pages/approvals/ApprovalDecisionButtons'

describe('ApprovalDecisionButtons', () => {
  it('labels the pair Approve / Reject for a non-failed run', () => {
    render(
      <ApprovalDecisionButtons isFailed={false} onApprove={vi.fn()} onReject={vi.fn()} />,
    )
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument()
  })

  it('relabels the pair Acknowledge / Retry for a failed run', () => {
    render(
      <ApprovalDecisionButtons isFailed onApprove={vi.fn()} onReject={vi.fn()} />,
    )
    expect(screen.getByRole('button', { name: 'Acknowledge' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
  })

  it('fires the matching callback on click', () => {
    const onApprove = vi.fn()
    const onReject = vi.fn()
    render(
      <ApprovalDecisionButtons isFailed={false} onApprove={onApprove} onReject={onReject} />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    fireEvent.click(screen.getByRole('button', { name: 'Reject' }))
    expect(onApprove).toHaveBeenCalledTimes(1)
    expect(onReject).toHaveBeenCalledTimes(1)
  })
})
