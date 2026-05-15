import type { Meta, StoryObj } from '@storybook/react'
import { TaskDetailPanel } from './TaskDetailPanel'
import type { DashboardTask } from '@/api/types/tasks'

const mockTask: DashboardTask = {
  id: 'task-1',
  title: 'Implement authentication flow',
  description: 'Build login, signup, and password reset with JWT tokens and refresh token rotation.',
  type: 'development',
  status: 'in_progress',
  priority: 'high',
  project: 'test-project',
  created_by: 'agent-cto',
  assigned_to: 'agent-eng-lead',
  reviewers: ['agent-qa'],
  dependencies: ['task-0', 'task-2'],
  artifacts_expected: [{ path: 'auth-module', type: 'code' as const }],
  acceptance_criteria: [
    { description: 'Login works with valid credentials', met: true },
    { description: 'Password reset sends email', met: false },
    { description: 'JWT refresh rotation implemented', met: false },
  ],
  estimated_complexity: 'complex',
  budget_limit: 10,
  cost: 3.45,
  deadline: '2026-04-01T00:00:00.000Z',
  max_retries: 3,
  parent_task_id: null,
  delegation_chain: [],
  task_structure: 'sequential',
  coordination_topology: 'auto',
  middleware_override: null,
  source: null,
  metadata: {},
  version: 3,
  created_at: '2026-03-25T10:00:00.000Z',
  updated_at: '2026-03-27T14:30:00.000Z',
}

const noop = async () => {}
const noopSentinel = async () => true

const meta = {
  title: 'Tasks/TaskDetailPanel',
  component: TaskDetailPanel,
  tags: ['autodocs'],
  parameters: { layout: 'fullscreen', a11y: { test: 'error' } },
} satisfies Meta<typeof TaskDetailPanel>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: {
    task: mockTask,
    onClose: () => {},
    onUpdate: noop,
    onTransition: noop,
    onCancel: noopSentinel,
    onDelete: noopSentinel,
  },
}

export const Loading: Story = {
  args: {
    task: mockTask,
    onClose: () => {},
    onUpdate: noop,
    onTransition: noop,
    onCancel: noopSentinel,
    onDelete: noopSentinel,
    loading: true,
  },
}

export const CompletedTask: Story = {
  args: {
    task: { ...mockTask, status: 'completed' },
    onClose: () => {},
    onUpdate: noop,
    onTransition: noop,
    onCancel: noopSentinel,
    onDelete: noopSentinel,
  },
}
