import type { Meta, StoryObj } from '@storybook/react'
import { fn } from 'storybook/test'
import { TaskCancelDialog } from './TaskCancelDialog'

const meta = {
  title: 'Pages/Tasks/TaskCancelDialog',
  component: TaskCancelDialog,
  args: {
    onOpenChange: fn(),
    onConfirm: fn(() => Promise.resolve(true)),
  },
} satisfies Meta<typeof TaskCancelDialog>

export default meta
type Story = StoryObj<typeof meta>

export const Open: Story = {
  args: { open: true },
}

export const Closed: Story = {
  args: { open: false },
}
