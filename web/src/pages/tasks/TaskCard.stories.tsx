import type { Meta, StoryObj } from '@storybook/react'
import { TaskCard } from './TaskCard'
import type { Task } from '@/api/types/tasks'

function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    id: 'task-1',
    title: 'Implement authentication flow',
    description: 'Build login, signup, and password reset with JWT tokens and refresh token rotation.',
    type: 'development',
    status: 'in_progress',
    priority: 'high',
    project: 'test-project',
    created_by: 'agent-cto',
    assigned_to: 'agent-eng-lead',
    reviewers: [],
    dependencies: ['task-0'],
    artifacts_expected: [],
    acceptance_criteria: [],
    estimated_complexity: 'complex',
    stakes: 'normal',
    budget_limit: 10,
    cost: 3.45,
    deadline: new Date(Date.now() + 86400000 * 2).toISOString(),
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
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  }
}

const meta = {
  title: 'Tasks/TaskCard',
  component: TaskCard,
  tags: ['autodocs'],
  decorators: [(Story) => <div className="max-w-[280px]"><Story /></div>],
} satisfies Meta<typeof TaskCard>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: { task: makeTask(), onSelect: () => {} },
}

export const MinimalFields: Story = {
  args: { task: makeTask({ assigned_to: null, deadline: null, cost: undefined, dependencies: [] }), onSelect: () => {} },
}

export const CriticalPriority: Story = {
  args: { task: makeTask({ priority: 'critical', status: 'blocked' }), onSelect: () => {} },
}

export const Completed: Story = {
  args: { task: makeTask({ status: 'completed', priority: 'low' }), onSelect: () => {} },
}

export const LongTitle: Story = {
  args: { task: makeTask({ title: 'This is a very long task title that should be truncated after two lines of text to keep the card compact and readable' }), onSelect: () => {} },
}
