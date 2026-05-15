import type { Meta, StoryObj } from '@storybook/react'
import { TaskHistory } from './TaskHistory'
import type { Task } from '@/api/types/tasks'

const FIXED_BASE = new Date('2026-03-26T12:00:00.000Z')

function makeTask(overrides: Partial<Task> & { id: string; title: string }): Task {
  return {
    description: 'Test task',
    type: 'development',
    status: 'completed',
    priority: 'medium',
    project: 'main',
    created_by: 'system',
    assigned_to: 'Alice Smith',
    reviewers: [],
    dependencies: [],
    artifacts_expected: [],
    acceptance_criteria: [],
    estimated_complexity: 'medium',
    budget_limit: 10,
    deadline: null,
    max_retries: 3,
    parent_task_id: null,
    delegation_chain: [],
    task_structure: null,
    coordination_topology: 'sas',
    middleware_override: null,
    source: null,
    metadata: {},
    created_at: new Date(FIXED_BASE.getTime() - 3_600_000).toISOString(),
    updated_at: FIXED_BASE.toISOString(),
    ...overrides,
  }
}

const tasks: Task[] = [
  makeTask({ id: 't1', title: 'Implement auth module', type: 'development', status: 'completed', created_at: new Date(FIXED_BASE.getTime() - 7_200_000).toISOString() }),
  makeTask({ id: 't2', title: 'Design review', type: 'review', status: 'completed', created_at: new Date(FIXED_BASE.getTime() - 3_600_000).toISOString() }),
  makeTask({ id: 't3', title: 'Research caching strategy', type: 'research', status: 'in_progress', created_at: new Date(FIXED_BASE.getTime() - 1_800_000).toISOString(), updated_at: FIXED_BASE.toISOString() }),
  makeTask({ id: 't4', title: 'Fix login bug', type: 'development', status: 'completed', created_at: new Date(FIXED_BASE.getTime() - 600_000).toISOString() }),
]

const meta = {
  title: 'Agents/TaskHistory',
  component: TaskHistory,
  decorators: [(Story) => <div className="p-6 max-w-lg"><Story /></div>],
} satisfies Meta<typeof TaskHistory>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = { args: { tasks } }
export const Empty: Story = { args: { tasks: [] } }
export const SingleTask: Story = { args: { tasks: [tasks[0]!] } }
