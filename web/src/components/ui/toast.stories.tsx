import type { Meta, StoryObj } from '@storybook/react'
import { useEffect } from 'react'
import { useToastStore } from '@/stores/toast'
import { Toast, ToastContainer } from './toast'

const meta = {
  title: 'Feedback/Toast',
  component: Toast,
  tags: ['autodocs'],
  parameters: {
    layout: 'padded',
  },
} satisfies Meta<typeof Toast>

export default meta
type Story = StoryObj<typeof meta>

export const Success: Story = {
  args: {
    toast: { id: '1', variant: 'success', title: 'Changes saved' },
    onDismiss: () => {},
  },
}

export const Error: Story = {
  args: {
    toast: { id: '2', variant: 'error', title: 'Failed to save' },
    onDismiss: () => {},
  },
}

export const Warning: Story = {
  args: {
    toast: { id: '3', variant: 'warning', title: 'Budget nearing limit' },
    onDismiss: () => {},
  },
}

export const InfoToast: Story = {
  args: {
    toast: { id: '4', variant: 'info', title: 'Agent started' },
    onDismiss: () => {},
  },
}

export const WithDescription: Story = {
  args: {
    toast: {
      id: '5',
      variant: 'error',
      title: 'Connection lost',
      description: 'Attempting to reconnect in 5 seconds...',
    },
    onDismiss: () => {},
  },
}

export const LongTitle: Story = {
  args: {
    toast: {
      id: '6',
      variant: 'warning',
      title:
        'The provider rejected the request because the configured rate limit for this workspace was exceeded and no fallback provider is available',
      description:
        'This title is intentionally long to exercise wrapping and truncation behaviour in the toast.',
    },
    onDismiss: () => {},
  },
}

export const WithAction: Story = {
  args: {
    toast: {
      id: '7',
      variant: 'error',
      title: 'Failed to publish workflow',
      description: 'The activation request timed out.',
      action: { label: 'Retry', onClick: () => {} },
    },
    onDismiss: () => {},
  },
}

export const AllVariants: Story = {
  args: {
    toast: { id: '1', variant: 'success', title: 'All variants' },
    onDismiss: () => {},
  },
  render: () => (
    <div className="flex flex-col gap-2">
      <Toast
        toast={{ id: '1', variant: 'success', title: 'Changes saved' }}
        onDismiss={() => {}}
      />
      <Toast
        toast={{ id: '2', variant: 'error', title: 'Failed to save' }}
        onDismiss={() => {}}
      />
      <Toast
        toast={{ id: '3', variant: 'warning', title: 'Budget nearing limit' }}
        onDismiss={() => {}}
      />
      <Toast
        toast={{ id: '4', variant: 'info', title: 'Agent started' }}
        onDismiss={() => {}}
      />
    </div>
  ),
}

function StackedDemo() {
  useEffect(() => {
    useToastStore.getState().dismissAll()
    useToastStore.getState().add({ variant: 'success', title: 'Task completed', duration: 60000 })
    useToastStore.getState().add({ variant: 'info', title: 'Agent assigned', duration: 60000 })
    useToastStore
      .getState()
      .add({ variant: 'warning', title: 'Budget at 80%', duration: 60000 })
    return () => useToastStore.getState().dismissAll()
  }, [])

  return <ToastContainer />
}

export const Stacked: Story = {
  render: () => <StackedDemo />,
  args: {
    toast: { id: '1', variant: 'success', title: 'Stacked' },
    onDismiss: () => {},
  },
  parameters: { layout: 'fullscreen' },
}
