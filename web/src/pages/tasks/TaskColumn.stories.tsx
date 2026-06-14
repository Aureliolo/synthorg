import type { Meta, StoryObj } from '@storybook/react'
import { DndContext } from '@dnd-kit/core'
import { TaskColumn } from './TaskColumn'
import { KANBAN_COLUMNS } from '@/utils/tasks'
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
    requested_by_user_id: null,
    reviewers: [],
    dependencies: [],
    artifacts_expected: [],
    acceptance_criteria: [],
    estimated_complexity: 'medium',
    stakes: 'normal',
    budget_limit: 10,
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
    created_at: '2026-03-20T10:00:00Z',
    updated_at: '2026-03-25T14:00:00Z',
    ...overrides,
  }
}

const meta = {
  title: 'Tasks/TaskColumn',
  component: TaskColumn,
  tags: ['autodocs'],
  decorators: [(Story) => <DndContext><div className="w-72"><Story /></div></DndContext>],
} satisfies Meta<typeof TaskColumn>

export default meta
type Story = StoryObj<typeof meta>

const inProgressColumn = KANBAN_COLUMNS.find((c) => c.id === 'in_progress')
if (!inProgressColumn) throw new Error('Missing in_progress column in KANBAN_COLUMNS')

export const WithTasks: Story = {
  args: {
    column: inProgressColumn,
    tasks: [
      makeTask('t1', 'Build API endpoints'),
      makeTask('t2', 'Design UI components', { priority: 'high' }),
      makeTask('t3', 'Write unit tests', { priority: 'low', assigned_to: 'agent-qa' }),
    ],
    onSelectTask: () => {},
  },
}

export const Empty: Story = {
  args: {
    column: inProgressColumn,
    tasks: [],
    onSelectTask: () => {},
  },
}
