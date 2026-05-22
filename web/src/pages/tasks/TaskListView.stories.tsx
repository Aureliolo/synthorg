import type { Meta, StoryObj } from '@storybook/react'
import { TaskListView } from './TaskListView'
import type { DashboardTask } from '@/api/types/tasks'

function makeTask(id: string, title: string, overrides: Partial<DashboardTask> = {}): DashboardTask {
  return {
    id,
    title,
    description: 'Task description',
    type: 'development',
    status: 'in_progress',
    priority: 'medium',
    project: 'test-project',
    created_by: 'agent-cto',
    assigned_to: 'agent-eng',
    reviewers: [],
    dependencies: [],
    artifacts_expected: [],
    acceptance_criteria: [],
    estimated_complexity: 'medium',
    stakes: 'normal',
    budget_limit: 10,
    cost: 2.50,
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
    ...overrides,
  }
}

const meta = {
  title: 'Tasks/TaskListView',
  component: TaskListView,
  tags: ['autodocs'],
} satisfies Meta<typeof TaskListView>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: {
    tasks: [
      makeTask('t1', 'Build API endpoints', { priority: 'high', status: 'in_progress' }),
      makeTask('t2', 'Design components', { priority: 'medium', status: 'in_review', assigned_to: 'agent-designer' }),
      makeTask('t3', 'Write docs', { priority: 'low', status: 'completed', type: 'research' }),
      makeTask('t4', 'Security audit', { priority: 'critical', status: 'blocked', assigned_to: null }),
    ],
    onSelectTask: () => {},
  },
}

export const Empty: Story = {
  args: { tasks: [], onSelectTask: () => {} },
}
