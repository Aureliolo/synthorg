import { useState } from 'react'
import type { Meta, StoryObj } from '@storybook/react-vite'
import { Button } from './button'
import { Collapsible } from './collapsible'
import { SkeletonText } from './skeleton'

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

// Gated trigger: the section cannot be toggled while a parent save is in flight.
export const Disabled: Story = {
  args: {
    title: 'Advanced settings',
    summary: 'Saving…',
    disabled: true,
    children: 'The trigger is dimmed and non-interactive until the save settles.',
  },
}

// Async body content: a skeleton fills the expanded section while it loads.
export const LoadingBody: Story = {
  args: {
    title: 'Live activity',
    children: <SkeletonText lines={3} />,
  },
}

// Controlled mode: a parent owns the open state (e.g. derived from a URL
// param), wiring the `open` + `onOpenChange` pair instead of `defaultOpen`.
function ControlledCollapsible() {
  const [open, setOpen] = useState(false)
  return (
    <div className="flex flex-col gap-2">
      <Button
        size="sm"
        variant="outline"
        className="self-start"
        onClick={() => setOpen((prev) => !prev)}
      >
        {open ? 'Collapse from parent' : 'Expand from parent'}
      </Button>
      <Collapsible title="Controlled section" open={open} onOpenChange={setOpen}>
        Toggled by the parent.
      </Collapsible>
    </div>
  )
}

export const Controlled: Story = {
  args: { title: 'Controlled section', children: 'Toggled by the parent.' },
  render: () => <ControlledCollapsible />,
  parameters: { controls: { disable: true } },
}
