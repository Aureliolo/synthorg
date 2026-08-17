import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'
import { OrgPulseSection, type OrgPulseSectionProps } from '@/pages/dashboard/OrgPulseSection'
import type { AgentActivity } from '@/api/types/cockpit'
import type { Blocker } from '@/utils/org-pulse'

function activity(overrides: Partial<AgentActivity> = {}): AgentActivity {
  return {
    agent_id: 'agent-1',
    agent_name: 'Anica Hocevar',
    task_id: 'task-1',
    task_title: 'Wire the login page',
    status: 'in_progress',
    turn_count: 3,
    cost: 0.42,
    last_active: null,
    execution_id: null,
    is_stuck: false,
    is_runaway: false,
    ...overrides,
  }
}

function blocker(overrides: Partial<Blocker> = {}): Blocker {
  return {
    id: 'subsystem:memory_backend',
    severity: 'warning',
    title: 'Memory Backend is blocked',
    detail: 'memory.embedder_model is unset',
    href: '/settings',
    ...overrides,
  }
}

function renderPanel(overrides: Partial<OrgPulseSectionProps> = {}) {
  const props: OrgPulseSectionProps = {
    running: [],
    queue: { queued: 0, idleAgents: 0 },
    blockers: [],
    loading: false,
    ...overrides,
  }
  return render(
    <MemoryRouter>
      <OrgPulseSection {...props} />
    </MemoryRouter>,
  )
}

describe('OrgPulseSection', () => {
  it('names the running task and whoever is on it', () => {
    renderPanel({ running: [activity()] })
    expect(screen.getByText('Wire the login page')).toBeInTheDocument()
    expect(screen.getByText(/Anica Hocevar/)).toBeInTheDocument()
  })

  it('never shows an agent or task id', () => {
    renderPanel({ running: [activity()] })
    // The panel is handed both, and must render neither.
    expect(screen.queryByText(/agent-1/)).not.toBeInTheDocument()
    expect(screen.queryByText(/task-1/)).not.toBeInTheDocument()
  })

  it('words an unresolved agent rather than printing its key', () => {
    renderPanel({ running: [activity({ agent_name: null })] })
    expect(screen.getByText(/Unknown agent/)).toBeInTheDocument()
  })

  it('says so plainly when nothing is running', () => {
    renderPanel({ queue: { queued: 3, idleAgents: 7 } })
    expect(screen.getByText('Nothing is running.')).toBeInTheDocument()
    expect(screen.getByText(/3 queued/)).toBeInTheDocument()
    expect(screen.getByText(/7 agents idle/)).toBeInTheDocument()
  })

  it('flags a runaway run', () => {
    renderPanel({ running: [activity({ is_runaway: true })] })
    expect(screen.getByText('runaway')).toBeInTheDocument()
  })

  it('shows a blocker with the reason its own subsystem gave', () => {
    renderPanel({ blockers: [blocker()] })
    expect(screen.getByText('Memory Backend is blocked')).toBeInTheDocument()
    expect(screen.getByText('memory.embedder_model is unset')).toBeInTheDocument()
  })

  it('gives a real all-clear rather than a zero', () => {
    renderPanel({ running: [activity()] })
    expect(screen.getByText('Nothing is blocking progress')).toBeInTheDocument()
    // The panel this replaced rendered "0%" over a column of "N/A" here.
    expect(screen.queryByText('0%')).not.toBeInTheDocument()
    expect(screen.queryByText('N/A')).not.toBeInTheDocument()
  })

  it('links a blocker to somewhere the operator can act', () => {
    renderPanel({ blockers: [blocker({ href: '/settings' })] })
    expect(screen.getByRole('link', { name: 'Go there' })).toHaveAttribute(
      'href',
      '/settings',
    )
  })

  it('omits the link when there is nowhere to go', () => {
    renderPanel({ blockers: [blocker({ href: null })] })
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })
})
