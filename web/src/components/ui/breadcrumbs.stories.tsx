import type { Meta, StoryObj } from '@storybook/react'
import { MemoryRouter } from 'react-router'
import { Breadcrumbs } from './breadcrumbs'

const meta = {
  title: 'Navigation/Breadcrumbs',
  component: Breadcrumbs,
  tags: ['autodocs'],
  parameters: { layout: 'padded' },
  decorators: [
    (Story) => (
      <MemoryRouter>
        <Story />
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof Breadcrumbs>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: {
    items: [
      { label: 'Projects', to: '/projects' },
      { label: 'Phoenix Re-platform' },
    ],
  },
}

export const ThreeLevels: Story = {
  args: {
    items: [
      { label: 'Org', to: '/org' },
      { label: 'Engineering', to: '/org/engineering' },
      { label: 'Platform Team' },
    ],
  },
}

export const FiveLevelsCollapsed: Story = {
  args: {
    items: [
      { label: 'Settings', to: '/settings' },
      { label: 'Namespaces', to: '/settings/namespaces' },
      { label: 'Coordination', to: '/settings/namespaces/coordination' },
      { label: 'Engineering Override', to: '/settings/namespaces/coordination/engineering' },
      { label: 'Wave Concurrency' },
    ],
    maxItems: 4,
  },
}

export const DeepCollapsed: Story = {
  args: {
    items: [
      { label: 'Level 1', to: '/l1' },
      { label: 'Level 2', to: '/l2' },
      { label: 'Level 3', to: '/l3' },
      { label: 'Level 4', to: '/l4' },
      { label: 'Level 5', to: '/l5' },
      { label: 'Level 6', to: '/l6' },
      { label: 'Level 7 (current)' },
    ],
    maxItems: 4,
  },
}

export const SingleItem: Story = {
  args: {
    items: [{ label: 'Dashboard' }],
  },
}

export const CurrentPageOnly: Story = {
  args: {
    items: [
      { label: 'Tasks', to: '/tasks' },
      { label: 'T-1234: Implement auth flow' },
    ],
  },
}

/**
 * Empty list: the component returns `null` when no items are supplied so the
 * caller can safely conditionalise on a loading or not-yet-resolved state
 * without wrapping the component in its own gate.
 */
export const Empty: Story = {
  args: {
    items: [],
  },
  parameters: {
    docs: {
      description: {
        story: 'With an empty `items` array, `Breadcrumbs` renders nothing (returns `null`).',
      },
    },
  },
}
