import type { Meta, StoryObj } from '@storybook/react'
import { action } from 'storybook/actions'
import { TaskCreateDialog } from './TaskCreateDialog'
import type { TaskBoardSubmissionResponse } from '@/api/types/tasks'

const meta = {
  title: 'Tasks/TaskCreateDialog',
  component: TaskCreateDialog,
  tags: ['autodocs'],
  parameters: { layout: 'fullscreen' },
} satisfies Meta<typeof TaskCreateDialog>

export default meta
type Story = StoryObj<typeof meta>

// Sentinel-return stub: onCreate now resolves to the 202
// ``TaskBoardSubmissionResponse`` envelope on success or ``null`` on
// failure (the spine creates the task in the background).
const nullCreate = (): Promise<TaskBoardSubmissionResponse | null> => Promise.resolve(null)

export const Open: Story = {
  args: {
    open: true,
    onOpenChange: () => {},
    onCreate: (data) => {
      action('onCreate')(data)
      return Promise.resolve(null)
    },
  },
}

export const Closed: Story = {
  args: {
    open: false,
    onOpenChange: () => {},
    onCreate: nullCreate,
  },
}
