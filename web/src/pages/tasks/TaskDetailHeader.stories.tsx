import type { Meta, StoryObj } from '@storybook/react'
import { TaskDetailHeader } from './TaskDetailHeader'
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
  reviewers: [],
  dependencies: [],
  artifacts_expected: [],
  acceptance_criteria: [],
  estimated_complexity: 'medium',
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
  created_at: '2026-04-19T00:00:00Z',
  updated_at: '2026-04-19T00:00:00Z',
}

const meta = {
  title: 'Pages/Tasks/TaskDetailHeader',
  component: TaskDetailHeader,
} satisfies Meta<typeof TaskDetailHeader>

export default meta
type Story = StoryObj<typeof meta>

export const InProgress: Story = {
  args: {
    task: baseTask,
  },
}

export const Blocked: Story = {
  args: {
    task: { ...baseTask, status: 'blocked', priority: 'critical' },
  },
}

export const Completed: Story = {
  args: {
    task: { ...baseTask, status: 'completed', priority: 'low' },
  },
}
