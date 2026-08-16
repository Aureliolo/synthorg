import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { PlanStaffingPanel } from '@/pages/plans/PlanStaffingPanel'

import { makePlan, makePlanItem } from '../../helpers/factories'

describe('PlanStaffingPanel', () => {
  it('renders nothing when there is nothing to staff', () => {
    const { container } = render(<PlanStaffingPanel plan={makePlan('p', { items: [] })} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('lists owners with their load and flags a bottleneck and unassigned work', () => {
    const plan = makePlan('p', {
      items: [
        makePlanItem('a', { owner: 'Backend', owner_name: 'Backend' }),
        makePlanItem('b', { owner: 'Backend', owner_name: 'Backend' }),
        makePlanItem('c', { owner: 'Backend', owner_name: 'Backend' }),
        makePlanItem('d', { owner: 'Design', owner_name: 'Design' }),
        makePlanItem('e', { owner: null }),
      ],
    })
    render(<PlanStaffingPanel plan={plan} />)
    expect(screen.getByText('Staffing')).toBeInTheDocument()
    expect(screen.getByText('2 owners')).toBeInTheDocument()
    expect(screen.getByText('Backend')).toBeInTheDocument()
    expect(screen.getByText('Bottleneck')).toBeInTheDocument()
    expect(screen.getByText(/1 item left/)).toBeInTheDocument()
  })
})
