import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { PlanReview } from '@/api/types/plans'
import { PlanReviewPanel } from '@/pages/plans/PlanReviewPanel'

function makeReview(overrides?: Partial<PlanReview>): PlanReview {
  return {
    verdict: 'concerns',
    summary: '1 of 2 reviewer(s) raised 1 finding(s) for the owner to address.',
    reviewed_at: '2026-07-01T10:00:00Z',
    reviewers: [
      {
        reviewer_role: 'CTO',
        reviewer_id: 'agent-cto',
        verdict: 'endorsed',
        findings: [],
      },
      {
        reviewer_role: 'CFO',
        reviewer_id: 'agent-cfo',
        verdict: 'concerns',
        findings: [
          { category: 'budget_concern', detail: 'Over the objective budget', item_id: null },
        ],
      },
    ],
    ...overrides,
  }
}

describe('PlanReviewPanel', () => {
  it('renders nothing when the backend recorded no review and no reason', () => {
    const { container } = render(
      <PlanReviewPanel review={null} absentReason={null} />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('says the plan carries no quality signal when a reason was recorded', () => {
    // The defect this closes: three different "no review" outcomes rendered
    // identically as nothing at all, so an operator approved a plan nothing
    // had checked with no way to know.
    render(
      <PlanReviewPanel
        review={null}
        absentReason="the panel ran and returned no verdict"
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent(
      'the panel ran and returned no verdict',
    )
    expect(screen.getByText(/carries no quality signal/)).toBeInTheDocument()
  })

  it('shows the consolidated verdict, each reviewer, and their findings', () => {
    render(<PlanReviewPanel review={makeReview()} absentReason={null} />)
    expect(screen.getByText('Stakeholder review')).toBeInTheDocument()
    expect(screen.getByText('CTO')).toBeInTheDocument()
    expect(screen.getByText('CFO')).toBeInTheDocument()
    expect(screen.getByText('Budget concern')).toBeInTheDocument()
    expect(screen.getByText('Over the objective budget')).toBeInTheDocument()
    // Endorsing reviewer shows the no-concerns affordance.
    expect(screen.getByText('No concerns raised.')).toBeInTheDocument()
  })

  it('renders the panel summary when present', () => {
    render(<PlanReviewPanel review={makeReview()} absentReason={null} />)
    expect(screen.getByText(/1 of 2 reviewer/)).toBeInTheDocument()
  })
})
