import type { Meta, StoryObj } from '@storybook/react'
import { fn } from 'storybook/test'
import { TaskDetailActions } from './TaskDetailActions'
import type { DashboardTask } from '@/api/types/tasks'

const baseTask: DashboardTask = {
  id: 'task-1',
  title: 'Implement new workflow engine',
  description: '',
  type: 'development',
  status: 'in_progress',
  priority: 'high',
  project: 'engine-rewrite',
  created_by: 'alice',
  assigned_to: 'bob',
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
  metadata: {},
  hard_ceiling: null,
  forecast_id: null,
  version: 1,
}

const meta = {
  title: 'Pages/Tasks/TaskDetailActions',
  component: TaskDetailActions,
  args: {
    onTransition: fn(),
    onRequestCancel: fn(),
    onRequestDelete: fn(),
  },
} satisfies Meta<typeof TaskDetailActions>

export default meta
type Story = StoryObj<typeof meta>

export const InProgress: Story = {
  args: {
    task: baseTask,
    transitioning: null,
  },
}

export const Transitioning: Story = {
  args: {
    task: baseTask,
    transitioning: 'completed',
  },
}

export const CompletedTerminal: Story = {
  args: {
    task: { ...baseTask, status: 'completed' },
    transitioning: null,
  },
}
