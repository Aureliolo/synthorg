import type { Meta, StoryObj } from '@storybook/react-vite'

import { TaskProgress } from './task-progress'

const meta = {
  title: 'UI/TaskProgress',
  component: TaskProgress,
  tags: ['autodocs'],
} satisfies Meta<typeof TaskProgress>

export default meta
type Story = StoryObj<typeof meta>

export const Starting: Story = {
  args: { status: 'running', stages: [] },
}

export const Running: Story = {
  args: {
    status: 'running',
    stages: [
      { id: '1', label: 'Step 1', status: 'done', description: 'read_file, search' },
      { id: '2', label: 'Step 2', status: 'running', description: 'write_file' },
    ],
  },
}

export const Finished: Story = {
  args: {
    status: 'finished',
    stages: [
      { id: '1', label: 'Step 1', status: 'done', description: 'read_file' },
      { id: '2', label: 'Step 2', status: 'done', description: 'write_file' },
    ],
  },
}

export const Failed: Story = {
  args: {
    status: 'error',
    stages: [
      { id: '1', label: 'Step 1', status: 'done', description: 'read_file' },
      { id: '2', label: 'Step 2', status: 'failed', description: 'run_tests' },
    ],
  },
}

export const Disconnected: Story = {
  args: {
    status: 'disconnected',
    stages: [
      { id: '1', label: 'Step 1', status: 'done', description: 'read_file' },
      { id: '2', label: 'Step 2', status: 'done', description: 'write_file' },
    ],
  },
}
