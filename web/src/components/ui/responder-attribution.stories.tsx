import type { Meta, StoryObj } from '@storybook/react'

import { ResponderAttribution } from './responder-attribution'

const meta = {
  title: 'UI/ResponderAttribution',
  component: ResponderAttribution,
  tags: ['autodocs'],
  argTypes: {
    name: { control: 'text' },
    role: { control: 'text' },
    topic: { control: 'text' },
  },
} satisfies Meta<typeof ResponderAttribution>

export default meta
type Story = StoryObj<typeof meta>

export const Routed: Story = {
  args: { name: 'Casey', role: 'CFO', topic: 'budget' },
}

export const WithoutTopic: Story = {
  args: { name: 'Dana', role: 'CEO' },
}

export const TechnicalLead: Story = {
  args: { name: 'Tomas', role: 'CTO', topic: 'technical' },
}

export const AllRoles: Story = {
  args: { name: 'Casey', role: 'CFO' },
  render: () => (
    <div className="flex flex-col gap-3">
      <ResponderAttribution name="Casey" role="CFO" topic="budget" />
      <ResponderAttribution name="Dana" role="CEO" topic="strategy" />
      <ResponderAttribution name="Tomas" role="CTO" topic="technical" />
      <ResponderAttribution name="Dana" role="CEO" />
    </div>
  ),
}
