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
