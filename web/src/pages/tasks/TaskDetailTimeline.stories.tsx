import type { Meta, StoryObj } from '@storybook/react-vite'
import { TaskDetailTimeline } from './TaskDetailTimeline'
import type { DashboardTask } from '@/api/types/tasks'

const baseTask: DashboardTask = {
  id: 'task-1',
  title: 'Demo task',
  description: 'A demo task used for Storybook.',
  type: 'development',
  status: 'in_progress',
  priority: 'medium',
  project: 'demo-project',
  created_by: 'alice',
  assigned_to: 'bob',
  reviewers: [],
  dependencies: [],
  artifacts_expected: [],
  acceptance_criteria: [],
  estimated_complexity: 'medium',
  budget_limit: 0,
  deadline: null,
  max_retries: 0,
  parent_task_id: null,
  delegation_chain: [],
  task_structure: null,
  coordination_topology: 'sas',
  middleware_override: null,
  source: null,
  metadata: {},
  hard_ceiling: null,
  forecast_id: null,
  version: 1,
  created_at: '2026-04-19T09:00:00Z',
  updated_at: '2026-04-20T11:30:00Z',
}

const meta = {
  title: 'Pages/Tasks/TaskDetailTimeline',
  component: TaskDetailTimeline,
  parameters: { layout: 'padded' },
} satisfies Meta<typeof TaskDetailTimeline>

export default meta

type Story = StoryObj<typeof meta>

export const AssignedInProgress: Story = {
  args: { task: baseTask },
}

export const UnassignedNew: Story = {
  args: {
    task: {
      ...baseTask,
      assigned_to: null,
      status: 'created',
      updated_at: baseTask.created_at,
    },
  },
}

export const Completed: Story = {
  args: {
    task: {
      ...baseTask,
      status: 'completed',
      updated_at: '2026-04-20T13:45:00Z',
    },
  },
}
