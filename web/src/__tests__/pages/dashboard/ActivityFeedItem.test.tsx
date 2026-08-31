import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { ActivityFeedItem } from '@/pages/dashboard/ActivityFeedItem'
import type { ActivityItem } from '@/api/types/analytics'

function makeActivity(overrides: Partial<ActivityItem> = {}): ActivityItem {
  return {
    id: 'test-1',
    timestamp: '2026-03-26T10:00:00Z',
    agent_name: 'agent-cto',
    action_type: 'task.created',
    description: 'created a task',
    task_id: null,
    department: null,
    ...overrides,
  }
}

function renderWithRouter(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>)
}

describe('ActivityFeedItem', () => {
  it('renders agent name', () => {
    renderWithRouter(<ActivityFeedItem activity={makeActivity({ agent_name: 'alice' })} />)
    expect(screen.getByText('alice')).toBeInTheDocument()
  })

  it('renders description text', () => {
    renderWithRouter(<ActivityFeedItem activity={makeActivity({ description: 'deployed service' })} />)
    expect(screen.getByText('deployed service')).toBeInTheDocument()
  })

  it('renders relative timestamp', () => {
    renderWithRouter(<ActivityFeedItem activity={makeActivity()} />)
    // formatRelativeTime will produce some timestamp string
    const timestampEl = screen.getByTestId('activity-timestamp')
    expect(timestampEl).toBeInTheDocument()
  })

  it('handles null task_id without error', () => {
    renderWithRouter(<ActivityFeedItem activity={makeActivity({ task_id: null })} />)
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('renders task link when task_id is present', () => {
    renderWithRouter(<ActivityFeedItem activity={makeActivity({ task_id: 'task-42' })} />)
    const link = screen.getByRole('link')
    expect(link).toHaveAttribute('href', '/tasks/task-42')
  })

  it('surfaces a failed run outcome with the run-outcome badge', () => {
    renderWithRouter(
      <ActivityFeedItem
        activity={makeActivity({
          action_type: 'task.status_changed',
          description: 'Ship auth failed',
          run_outcome: 'failed',
        })}
      />,
    )
    expect(screen.getByText('Run failed')).toBeInTheDocument()
  })

  it('flags an empty run as having produced nothing', () => {
    renderWithRouter(
      <ActivityFeedItem
        activity={makeActivity({
          action_type: 'task.status_changed',
          run_outcome: 'empty',
        })}
      />,
    )
    expect(screen.getByText('Produced nothing')).toBeInTheDocument()
  })

  it('renders no run-outcome badge for a plain activity row', () => {
    renderWithRouter(<ActivityFeedItem activity={makeActivity()} />)
    expect(screen.queryByText('Run failed')).not.toBeInTheDocument()
    expect(screen.queryByText('Produced nothing')).not.toBeInTheDocument()
  })

  it('exposes the action-type dot to screen readers when there is no run outcome', () => {
    renderWithRouter(<ActivityFeedItem activity={makeActivity({ action_type: 'task.created' })} />)
    expect(screen.getByRole('img', { name: 'Action: task created' })).toBeInTheDocument()
  })

  it('hides the action-type dot from screen readers when a run outcome supplies its own label', () => {
    renderWithRouter(
      <ActivityFeedItem
        activity={makeActivity({
          action_type: 'task.status_changed',
          run_outcome: 'failed',
        })}
      />,
    )
    expect(screen.queryByRole('img', { name: /Action:/ })).not.toBeInTheDocument()
  })
})
