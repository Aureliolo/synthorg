import type { Meta, StoryObj } from '@storybook/react-vite'
import { Collapsible } from './collapsible'

const meta = {
  title: 'UI/Collapsible',
  component: Collapsible,
  parameters: { layout: 'padded' },
} satisfies Meta<typeof Collapsible>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: {
    title: 'Section title',
    children: 'Body content rendered when expanded.',
  },
}

export const WithSummary: Story = {
  args: {
    title: 'Spending breakdown',
    summary: '12 entries',
    children: 'Body content shows once the user expands the section.',
  },
}

export const CollapsedByDefault: Story = {
  args: {
    title: 'Advanced settings',
    defaultOpen: false,
    children: 'Hidden until the user clicks the trigger row.',
  },
}

export const LongBody: Story = {
  args: {
    title: 'Report sections',
    children: (
      <ul className="list-disc pl-4 text-sm text-text-secondary">
        <li>Spending summary by department</li>
        <li>Per-agent performance scorecard</li>
        <li>Task completion timeline</li>
        <li>Risk trend chart</li>
      </ul>
    ),
  },
}
