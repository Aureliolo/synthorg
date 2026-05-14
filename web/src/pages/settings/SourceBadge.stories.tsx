import type { Meta, StoryObj } from '@storybook/react'
import { SourceBadge } from './SourceBadge'

const meta = {
  title: 'Settings/SourceBadge',
  component: SourceBadge,
  parameters: { layout: 'centered' },
} satisfies Meta<typeof SourceBadge>

export default meta
type Story = StoryObj<typeof meta>

export const Database: Story = { args: { source: 'db' } }
export const Environment: Story = { args: { source: 'env' } }
export const DefaultRendersNull: Story = { args: { source: 'default' } }

export const AllSources: Story = {
  args: { source: 'db' },
  render: () => (
    <div className="flex items-center gap-2">
      <SourceBadge source="db" />
      <SourceBadge source="env" />
      {/* default renders nothing */}
      <SourceBadge source="default" />
    </div>
  ),
}
