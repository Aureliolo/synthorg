import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import * as fc from 'fast-check'
import { ActivityFeed } from '@/pages/dashboard/ActivityFeed'
import type { ActivityItem } from '@/api/types/analytics'

function makeActivities(count: number): ActivityItem[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `activity-${i}`,
    timestamp: new Date(Date.now() - i * 60_000).toISOString(),
    agent_name: `agent-${i}`,
    action_type: 'task.created' as const,
    description: `Action ${i}`,
    task_id: null,
    department: null,
  }))
}

function renderWithRouter(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>)
}

describe('ActivityFeed', () => {
  it('renders section title', () => {
    renderWithRouter(<ActivityFeed activities={[]} />)
    expect(screen.getByText('Live Activity')).toBeInTheDocument()
  })

  it('shows empty state when no activities', () => {
    renderWithRouter(<ActivityFeed activities={[]} />)
    expect(screen.getByText('No activity yet')).toBeInTheDocument()
  })

  it('renders activity items', () => {
    renderWithRouter(<ActivityFeed activities={makeActivities(3)} />)
    expect(screen.getByText('agent-0')).toBeInTheDocument()
    expect(screen.getByText('agent-1')).toBeInTheDocument()
    expect(screen.getByText('agent-2')).toBeInTheDocument()
  })

  it('caps displayed items at 10', () => {
    fc.assert(
      fc.property(fc.integer({ min: 0, max: 50 }), (count) => {
        const { unmount } = renderWithRouter(<ActivityFeed activities={makeActivities(count)} />)
        const visible = Math.min(count, 10)
        for (let i = 0; i < visible; i++) {
          expect(screen.getByText(`agent-${i}`)).toBeInTheDocument()
        }
        if (count > 10) {
          expect(screen.queryByText('agent-10')).not.toBeInTheDocument()
        }
        unmount()
      }),
      { numRuns: 10 },
    )
  })
})
