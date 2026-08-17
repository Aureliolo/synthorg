import { useState } from 'react'
import type { Meta, StoryObj } from '@storybook/react'
import { TaskFilterBar } from './TaskFilterBar'
import type { TaskBoardFilters } from '@/utils/tasks'

const meta = {
  title: 'Tasks/TaskFilterBar',
  component: TaskFilterBar,
  tags: ['autodocs'],
} satisfies Meta<typeof TaskFilterBar>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: {
    filters: {},
    onFiltersChange: () => {},
    viewMode: 'board',
    onViewModeChange: () => {},
    onCreateTask: () => {},
    assignees: [
      { id: 'agent-cto', name: 'Chief Technology Officer' },
      { id: 'agent-eng-lead', name: 'Engineering Lead' },
      { id: 'agent-designer', name: 'Designer' },
    ],
    taskCount: 24,
  },
}

export const WithActiveFilters: Story = {
  args: {
    filters: { status: 'in_progress', priority: 'high' },
    onFiltersChange: () => {},
    viewMode: 'board',
    onViewModeChange: () => {},
    onCreateTask: () => {},
    assignees: [
      { id: 'agent-cto', name: 'Chief Technology Officer' },
      { id: 'agent-eng-lead', name: 'Engineering Lead' },
    ],
    taskCount: 5,
  },
}

function InteractiveFilterBar() {
  const [filters, setFilters] = useState<TaskBoardFilters>({})
  const [viewMode, setViewMode] = useState<'board' | 'list'>('board')
  return (
    <TaskFilterBar
      filters={filters}
      onFiltersChange={setFilters}
      viewMode={viewMode}
      onViewModeChange={setViewMode}
      onCreateTask={() => alert('Create task clicked')}
      assignees={[
        { id: 'agent-cto', name: 'Chief Technology Officer' },
        { id: 'agent-eng', name: 'Engineer' },
        { id: 'agent-qa', name: 'QA Engineer' },
      ]}
      taskCount={12}
    />
  )
}

export const Interactive: StoryObj = {
  render: () => <InteractiveFilterBar />,
}
