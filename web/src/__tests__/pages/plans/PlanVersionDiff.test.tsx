import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { PlanVersionSnapshot } from '@/api/types/plans'
import { PlanVersionDiff } from '@/pages/plans/PlanVersionDiff'

import { makePlan, makePlanItem } from '../../helpers/factories'

function snapshot(): PlanVersionSnapshot {
  return {
    version: 1,
    task_structure: 'sequential',
    captured_at: '2026-07-01T10:00:00Z',
    items: [
      makePlanItem('a', { title: 'Board', owner: 'A' }),
      makePlanItem('b', { title: 'Gone' }),
    ],
  }
}

describe('PlanVersionDiff', () => {
  it('renders nothing when there is no prior version', () => {
    const { container } = render(
      <PlanVersionDiff plan={makePlan('p', { version_history: [] })} />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('shows added, removed, and modified items against the last snapshot', () => {
    const plan = makePlan('p', {
      version: 2,
      version_history: [snapshot()],
      items: [
        makePlanItem('a', { title: 'Board', owner: 'B' }),
        makePlanItem('c', { title: 'New work' }),
      ],
    })
    render(<PlanVersionDiff plan={plan} />)
    expect(screen.getByText('Changes since last revision')).toBeInTheDocument()
    expect(screen.getByText('v1 to v2')).toBeInTheDocument()
    expect(screen.getByText('added')).toBeInTheDocument()
    expect(screen.getByText('removed')).toBeInTheDocument()
    expect(screen.getByText('modified')).toBeInTheDocument()
    expect(screen.getByText(/changed: owner/)).toBeInTheDocument()
  })
})
