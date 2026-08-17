import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'
import { ProjectPlanProgress } from '@/pages/projects/ProjectPlanProgress'
import type { ProjectProgress, ProjectProgressItem } from '@/api/types/projects'

function makeItem(overrides: Partial<ProjectProgressItem> = {}): ProjectProgressItem {
  return {
    item_id: 'item-a',
    title: 'Scaffold',
    kind: 'work',
    owner: null,
    owner_name: null,
    depends_on: [],
    task_id: 'task-a',
    task_status: 'completed',
    chosen_option_id: null,
    blocked_reason: null,
    done: true,
    on_critical_path: false,
    ...overrides,
  }
}

function makeProgress(overrides: Partial<ProjectProgress> = {}): ProjectProgress {
  return {
    project_id: 'proj-1',
    project_status: 'active',
    plan_id: 'plan-1',
    plan_status: 'executing',
    objective_title: 'Ship the initiative',
    items: [makeItem()],
    counts: { total: 1, done: 1, failed: 0, blocked: 0 },
    critical_path: [],
    contributors: [{ id: 'agent-eng', name: 'Engineer' }],
    ...overrides,
  }
}

function renderProgress(progress: ProjectProgress | null) {
  return render(
    <MemoryRouter>
      <ProjectPlanProgress progress={progress} />
    </MemoryRouter>,
  )
}

describe('ProjectPlanProgress', () => {
  it('renders the plan status and objective', () => {
    renderProgress(makeProgress())

    expect(screen.getByText('Executing')).toBeInTheDocument()
    expect(screen.getByText('Ship the initiative')).toBeInTheDocument()
  })

  it('shows the done count against the total', () => {
    renderProgress(
      makeProgress({
        items: [makeItem(), makeItem({ item_id: 'item-b', done: false })],
        counts: { total: 2, done: 1, failed: 0, blocked: 0 },
      }),
    )

    expect(screen.getByText('1/2')).toBeInTheDocument()
  })

  it('surfaces failed and blocked items as attention counts', () => {
    renderProgress(
      makeProgress({ counts: { total: 4, done: 1, failed: 2, blocked: 1 } }),
    )

    expect(screen.getByText('2 failed')).toBeInTheDocument()
    expect(screen.getByText('1 blocked')).toBeInTheDocument()
  })

  it('omits the attention counts when nothing needs attention', () => {
    renderProgress(makeProgress())

    expect(screen.queryByText(/failed/)).not.toBeInTheDocument()
    expect(screen.queryByText(/blocked/)).not.toBeInTheDocument()
  })

  it('marks items on the critical path', () => {
    renderProgress(
      makeProgress({
        items: [makeItem({ on_critical_path: true })],
        critical_path: ['item-a'],
      }),
    )

    expect(screen.getByText('Critical')).toBeInTheDocument()
  })

  it('labels a decision item by whether it is decided', () => {
    renderProgress(
      makeProgress({
        items: [
          makeItem({
            kind: 'decision',
            task_id: null,
            task_status: null,
            done: false,
          }),
        ],
        counts: { total: 1, done: 0, failed: 0, blocked: 0 },
      }),
    )

    expect(screen.getByText('Undecided')).toBeInTheDocument()
  })

  it('marks an item with no dispatched task', () => {
    renderProgress(
      makeProgress({
        items: [makeItem({ task_id: null, task_status: null, done: false })],
        counts: { total: 1, done: 0, failed: 0, blocked: 0 },
      }),
    )

    expect(screen.getByText('Not dispatched')).toBeInTheDocument()
  })

  it('renders an empty state when the project has no plan', () => {
    renderProgress(makeProgress({ plan_id: null }))

    expect(screen.getByText('No plan yet')).toBeInTheDocument()
  })

  it('renders an empty state before progress loads', () => {
    renderProgress(null)

    expect(screen.getByText('No plan yet')).toBeInTheDocument()
  })

  it('distinguishes a failed progress fetch from a project with no plan', () => {
    render(
      <MemoryRouter>
        <ProjectPlanProgress progress={null} failed />
      </MemoryRouter>,
    )

    expect(screen.getByText('Progress unavailable')).toBeInTheDocument()
    // Claiming "no plan yet" for a request that simply failed would tell the
    // operator something untrue about their initiative.
    expect(screen.queryByText('No plan yet')).not.toBeInTheDocument()
  })

  it('labels each task link with its item so they are distinguishable', () => {
    renderProgress(makeProgress())

    expect(
      screen.getByRole('link', { name: 'View task for Scaffold' }),
    ).toBeInTheDocument()
  })
})
