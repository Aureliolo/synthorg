import type { Meta, StoryObj } from '@storybook/react'
import { TaskDetailMetadata } from './TaskDetailMetadata'
import type { DashboardTask } from '@/api/types/tasks'

const baseTask: DashboardTask = {
  id: 'task-1',
  title: 'Implement new workflow engine',
  description: 'Port the legacy runner to the RFC-driven pipeline.',
  type: 'development',
  status: 'in_progress',
  priority: 'high',
  project: 'engine-rewrite',
  created_by: 'alice',
  assigned_to: 'bob',
  requested_by_user_id: null,
  reviewers: [],
  dependencies: [],
  artifacts_expected: [],
  acceptance_criteria: [],
  estimated_complexity: 'medium',
  stakes: 'normal',
  budget_limit: 100,
  deadline: null,
  max_retries: 3,
  parent_task_id: null,
  delegation_chain: [],
  task_structure: null,
  coordination_topology: 'auto',
  middleware_override: null,
  source: null,
  blocked_reason: null,
  metadata: {},
  hard_ceiling: null,
  hard_token_ceiling: null,
  forecast_id: null,
  plan_id: null,
  plan_item_id: null,
  cost: 12.4,
  version: 1,
  created_at: '2026-04-19T00:00:00Z',
  updated_at: '2026-04-19T00:00:00Z',
}

const meta = {
  title: 'Pages/Tasks/TaskDetailMetadata',
  component: TaskDetailMetadata,
} satisfies Meta<typeof TaskDetailMetadata>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: {
    task: baseTask,
  },
}

export const WithDependenciesAndCriteria: Story = {
  args: {
    task: {
      ...baseTask,
      dependencies: ['task-0', 'task-99'],
      acceptance_criteria: [
        { description: 'Tests pass', met: true },
        { description: 'Docs updated', met: false },
      ],
    },
  },
}

export const Unassigned: Story = {
  args: {
    task: { ...baseTask, assigned_to: null, cost: undefined },
  },
}

/** Parked on a person. The reason is what tells this apart from the next. */
export const BlockedOnAHuman: Story = {
  args: {
    task: { ...baseTask, status: 'blocked', blocked_reason: 'oracle_escalated' },
  },
}

/** Parked on a scheduler, which needs nothing from the operator. */
export const BlockedOnAWave: Story = {
  args: {
    task: { ...baseTask, status: 'blocked', blocked_reason: 'wave_released' },
  },
}

/** Blocked before the reason was recorded: the row says nothing, so nor does this. */
export const BlockedWithNoReasonRecorded: Story = {
  args: {
    task: { ...baseTask, status: 'blocked', blocked_reason: null },
  },
}
