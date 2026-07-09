import type { Meta, StoryObj } from '@storybook/react-vite'

import { ProvenanceBadge } from './provenance-badge'

const meta = {
  title: 'UI/ProvenanceBadge',
  component: ProvenanceBadge,
  tags: ['autodocs'],
} satisfies Meta<typeof ProvenanceBadge>

export default meta
type Story = StoryObj<typeof meta>

export const Measured: Story = {
  args: {
    className: 'border border-success/30 bg-success/10 text-success',
    title: 'Measured per-model benchmark scores',
    children: 'measured',
  },
}

export const Absent: Story = {
  args: {
    className: 'border border-border bg-muted text-muted-foreground',
    title: 'No measured benchmark scores yet',
    children: 'not measured',
  },
}
