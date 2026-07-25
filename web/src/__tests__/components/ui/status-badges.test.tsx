import { render, screen } from '@testing-library/react'
import { PlanStatusBadge } from '@/components/ui/plan-status-badge'
import { ProjectStatusBadge } from '@/components/ui/project-status-badge'
import { PLAN_STATUS_VALUES, PROJECT_STATUS_VALUES } from '@/api/types/enum-values.gen'

describe('PlanStatusBadge', () => {
  it.each(PLAN_STATUS_VALUES)('renders a label for %s', (status) => {
    render(<PlanStatusBadge status={status} />)
    expect(screen.getByText(/\w/)).toBeInTheDocument()
  })

  it('names the tail stages the loop added', () => {
    const { rerender } = render(<PlanStatusBadge status="integrating" />)
    expect(screen.getByText('Integrating')).toBeInTheDocument()
    rerender(<PlanStatusBadge status="evaluating" />)
    expect(screen.getByText('Evaluating')).toBeInTheDocument()
  })
})

describe('ProjectStatusBadge', () => {
  it.each(PROJECT_STATUS_VALUES)('labels %s by default', (status) => {
    // The palette has one "in flight" colour shared by active, integrating and
    // evaluating, so the label is what distinguishes them.
    render(<ProjectStatusBadge status={status} />)
    expect(screen.getByText(/\w/)).toBeInTheDocument()
  })

  it('names the tail stages the loop added', () => {
    const { rerender } = render(<ProjectStatusBadge status="integrating" />)
    expect(screen.getByText('Integrating')).toBeInTheDocument()
    rerender(<ProjectStatusBadge status="evaluating" />)
    expect(screen.getByText('Evaluating')).toBeInTheDocument()
  })

  it('keeps an accessible label when the visible one is suppressed', () => {
    render(<ProjectStatusBadge status="evaluating" showLabel={false} />)
    expect(screen.getByLabelText('Evaluating')).toBeInTheDocument()
  })
})
