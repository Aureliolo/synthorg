import { useState } from 'react'
import type { Meta, StoryObj } from '@storybook/react'
import { Drawer } from './drawer'
import { Button } from './button'

const meta = {
  title: 'UI/Drawer',
  component: Drawer,
  tags: ['autodocs'],
  parameters: {
    layout: 'fullscreen',
    a11y: { test: 'error' },
  },
} satisfies Meta<typeof Drawer>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: {
    open: true,
    onClose: () => {},
    title: 'Compare Templates',
    children: (
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Compare templates side by side to find the best fit for your organization.
        </p>
        <div className="grid grid-cols-2 gap-4">
          <div className="rounded-md border border-border p-3">
            <h3 className="text-sm font-semibold text-foreground">Tech Startup</h3>
            <p className="text-xs text-muted-foreground">5 agents, 3 departments</p>
          </div>
          <div className="rounded-md border border-border p-3">
            <h3 className="text-sm font-semibold text-foreground">Solo Founder</h3>
            <p className="text-xs text-muted-foreground">1 agent, 1 department</p>
          </div>
        </div>
      </div>
    ),
  },
}

function InteractiveDrawer() {
  const [open, setOpen] = useState(false)
  return (
    <div className="p-8">
      <Button onClick={() => setOpen(true)}>Open Drawer</Button>
      <Drawer open={open} onClose={() => setOpen(false)} title="Compare Templates">
        <p className="text-sm text-muted-foreground">Drawer content goes here.</p>
      </Drawer>
    </div>
  )
}

export const Interactive: Story = {
  args: { open: false, onClose: () => {}, title: 'Drawer', children: null },
  render: () => <InteractiveDrawer />,
}

export const LeftSide: Story = {
  args: {
    open: true,
    onClose: () => {},
    title: 'Navigation',
    side: 'left',
    children: (
      <div className="space-y-2">
        <p className="text-sm text-muted-foreground">Left-side drawer panel.</p>
        <ul className="space-y-1 text-sm">
          <li>Dashboard</li>
          <li>Settings</li>
          <li>Profile</li>
        </ul>
      </div>
    ),
  },
}

export const Headerless: Story = {
  args: {
    open: true,
    onClose: () => {},
    ariaLabel: 'Custom panel',
    contentClassName: 'p-0',
    children: (
      <div className="flex h-full flex-col">
        <div className="border-b border-border px-4 py-3">
          <span className="text-lg font-bold text-accent">Custom Header</span>
        </div>
        <div className="flex-1 p-4">
          <p className="text-sm text-muted-foreground">
            Headerless drawer with custom content and no default padding.
          </p>
        </div>
      </div>
    ),
  },
}

export const Compact: Story = {
  args: {
    open: true,
    onClose: () => {},
    title: 'Quick edit',
    width: 'compact',
    children: (
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Compact drawers are tuned for content-light flows (single-field
          edits, confirmations) where the default 45vw consumes too much
          horizontal space on narrow viewports.
        </p>
      </div>
    ),
  },
}

export const Narrow: Story = {
  args: {
    open: true,
    onClose: () => {},
    title: 'Narrow drawer',
    width: 'narrow',
    children: (
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Narrow drawer for mobile-friendly contexts.
        </p>
      </div>
    ),
  },
}

export const Wide: Story = {
  args: {
    open: true,
    onClose: () => {},
    title: 'Wide drawer',
    width: 'wide',
    children: (
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Wide drawer for template compare and other large forms.
        </p>
      </div>
    ),
  },
}

export const ScrollableContent: Story = {
  args: {
    open: true,
    onClose: () => {},
    title: 'Long Content',
    children: (
      <div className="space-y-4">
        {Array.from({ length: 20 }, (_, i) => (
          <div key={i} className="rounded-md border border-border p-3">
            <h3 className="text-sm font-semibold text-foreground">Item {i + 1}</h3>
            <p className="text-xs text-muted-foreground">Some content for item {i + 1}</p>
          </div>
        ))}
      </div>
    ),
  },
}
