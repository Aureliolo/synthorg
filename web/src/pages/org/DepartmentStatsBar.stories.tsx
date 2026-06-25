import type { Meta, StoryObj } from '@storybook/react'
import { DepartmentStatsBar } from './DepartmentStatsBar'

const meta = {
  title: 'OrgChart/DepartmentStatsBar',
  component: DepartmentStatsBar,
  tags: ['autodocs'],
  parameters: {
    a11y: { test: 'error' },
  },
  decorators: [
    (Story) => (
      <div className="max-w-md">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof DepartmentStatsBar>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: {
    activeCount: 4,
    cost7d: 45.8,
  },
}

export const NoCost: Story = {
  args: {
    activeCount: 1,
    cost7d: null,
  },
}

export const ZeroActive: Story = {
  args: {
    activeCount: 0,
    cost7d: 0,
  },
}
