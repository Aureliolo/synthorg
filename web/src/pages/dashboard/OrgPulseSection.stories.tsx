import type { Meta, StoryObj } from '@storybook/react'
import { MemoryRouter } from 'react-router'
import { OrgPulseSection } from './OrgPulseSection'
import type { AgentActivity } from '@/api/types/cockpit'
import type { Blocker } from '@/utils/org-pulse'

const RUNNING: AgentActivity[] = [
  {
    agent_id: 'agent-1',
    agent_name: 'Anica Hocevar',
    task_id: 'task-1',
    task_title: 'Wire the login page',
    status: 'in_progress',
    turn_count: 3,
    cost: 0.42,
    last_active: '2026-03-26T11:56:00Z',
    execution_id: 'exec-1',
    is_stuck: false,
    is_runaway: false,
  },
]

const BLOCKERS: Blocker[] = [
  {
    id: 'runs:unproductive',
    severity: 'critical',
    title: '5 of 5 runs produced nothing',
    detail: 'No run has produced output yet, so nothing the org started has landed.',
    href: '/tasks',
  },
  {
    id: 'subsystem:memory_backend',
    severity: 'warning',
    title: 'Memory Backend is blocked',
    detail: 'memory.embedder_model is unset',
    href: '/settings',
  },
  {
    id: 'blocked:reviewer_unstaffed',
    severity: 'warning',
    title: '4 tasks blocked',
    detail: 'waiting for a Completion Reviewer to be staffed',
    href: '/tasks',
  },
]

const meta = {
  title: 'Dashboard/OrgPulseSection',
  component: OrgPulseSection,
  decorators: [
    (Story) => (
      <MemoryRouter>
        <div className="max-w-xl p-6">
          <Story />
        </div>
      </MemoryRouter>
    ),
  ],
  parameters: { a11y: { test: 'error' } },
  args: {
    runningError: null,
    blockersError: null,
    runningLoading: false,
    blockersLoading: false,
  },
} satisfies Meta<typeof OrgPulseSection>

export default meta
type Story = StoryObj<typeof meta>

/** The state that prompted this panel: work queued, nothing landing. */
export const Stalled: Story = {
  args: {
    running: RUNNING,
    queue: { queued: 15, idleAgents: 11 },
    blockers: BLOCKERS,
  },
}

export const AllClear: Story = {
  args: {
    running: RUNNING,
    queue: { queued: 2, idleAgents: 4 },
    blockers: [],
  },
}

export const NothingRunning: Story = {
  args: {
    running: [],
    queue: { queued: 0, idleAgents: 12 },
    blockers: [],
  },
}

export const RunawayRun: Story = {
  args: {
    running: [{ ...RUNNING[0]!, is_runaway: true, turn_count: 41 }],
    queue: { queued: 0, idleAgents: 11 },
    blockers: [],
  },
}

/** Mutually exclusive with runaway, and never shown together with it. */
export const StuckRun: Story = {
  args: {
    running: [{ ...RUNNING[0]!, is_stuck: true, turn_count: 18 }],
    queue: { queued: 3, idleAgents: 9 },
    blockers: [],
  },
}

export const Loading: Story = {
  args: {
    running: [],
    queue: { queued: 0, idleAgents: 0 },
    blockers: [],
    runningLoading: true,
    blockersLoading: true,
  },
}

/**
 * The state the panel must never render as an all-clear.
 *
 * An empty blockers list caused by a failed read is not evidence that nothing
 * is blocking progress, so each half says which read it is missing.
 */
export const ReadsFailed: Story = {
  args: {
    running: [],
    queue: { queued: 0, idleAgents: 0 },
    blockers: [],
    runningError: 'Request failed with status code 503',
    blockersError: 'Request failed with status code 503',
  },
}

/** One half stale, the other fine: the errors are tracked separately. */
export const BlockersUnreadable: Story = {
  args: {
    running: RUNNING,
    queue: { queued: 15, idleAgents: 11 },
    blockers: [],
    blockersError: 'Could not reach the subsystems endpoint',
  },
}
