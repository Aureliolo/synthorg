import type { Meta, StoryObj } from '@storybook/react'
import { AlertCircle, Inbox, Search, Users } from 'lucide-react'
import { EmptyState } from './empty-state'

const meta = {
  title: 'Feedback/EmptyState',
  component: EmptyState,
  tags: ['autodocs'],
  parameters: {
    layout: 'padded',
  },
} satisfies Meta<typeof EmptyState>

export default meta
type Story = StoryObj<typeof meta>

export const NoData: Story = {
  args: {
    icon: Inbox,
    title: 'No agents found',
    description: 'Your organisation has no agents yet. Create one to get started.',
  },
}

export const NoResults: Story = {
  args: {
    icon: Search,
    title: 'No results',
    description: 'Try adjusting your search or filter criteria.',
  },
}

export const WithAction: Story = {
  args: {
    icon: Users,
    title: 'No agents found',
    description: 'Create your first agent to start building your organisation.',
    action: {
      label: 'Create Agent',
      onClick: () => {},
    },
  },
}

export const ErrorVariant: Story = {
  args: {
    icon: AlertCircle,
    title: 'Failed to load data',
    description: 'Something went wrong. Please try again.',
    action: {
      label: 'Retry',
      onClick: () => {},
    },
  },
}

export const Minimal: Story = {
  args: {
    title: 'Nothing here yet',
  },
}

export const WithLearnMore: Story = {
  args: {
    icon: Inbox,
    title: 'No custom rules defined',
    description: 'Custom rules let you enforce project-specific review criteria.',
    learnMore: {
      label: 'Learn about custom rules',
      href: 'https://synthorg.io/docs/custom-rules',
      external: true,
    },
  },
}

export const WithActionAndLearnMore: Story = {
  args: {
    icon: Users,
    title: 'No projects yet',
    description: 'Projects group related tasks, agents, and artifacts together.',
    action: {
      label: 'Create project',
      onClick: () => {},
    },
    learnMore: {
      href: 'https://synthorg.io/docs/projects',
      external: true,
    },
  },
}
