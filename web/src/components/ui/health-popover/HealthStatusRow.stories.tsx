import type { Meta, StoryObj } from '@storybook/react'
import { MemoryRouter, Link } from 'react-router'
import { Brain, Database, Wifi } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { HealthStatusRow } from './HealthStatusRow'

const meta = {
  title: 'Overlays/HealthPopover/HealthStatusRow',
  component: HealthStatusRow,
  tags: ['autodocs'],
  parameters: {
    layout: 'centered',
    a11y: { test: 'error' },
  },
  decorators: [
    (Story) => (
      <MemoryRouter>
        <div className="w-80">
          <Story />
        </div>
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof HealthStatusRow>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: {
    icon: Wifi,
    label: 'Backend API',
    description: 'HTTP layer serving the dashboard and controller endpoints.',
    state: 'ok',
  },
}

export const Degraded: Story = {
  args: {
    ...Default.args,
    state: 'degraded',
    detail: 'auto-reconnecting',
  },
}

export const Down: Story = {
  args: {
    icon: Database,
    label: 'Persistence',
    description: 'Configured persistence backend. Writes and queries round-trip.',
    state: 'down',
    action: (
      <Button type="button" variant="outline" size="sm" onClick={() => undefined}>
        Retry now
      </Button>
    ),
  },
}

// A degraded card carrying its remedy. The state a card is most likely to sit
// in is also the one an operator can act on, so it has to render the action.
export const DegradedWithRemediation: Story = {
  args: {
    icon: Brain,
    label: 'Memory',
    description:
      'Org, agent, and project recall injected into working agents. Durable requires an embedding model.',
    state: 'degraded',
    detail: 'no embedding model chosen',
    action: (
      <Button variant="outline" size="sm" asChild>
        <Link to="/settings/memory?q=embedder_model">Choose an embedding model</Link>
      </Button>
    ),
  },
}

// Two actions side by side: one that acts on the spot and one that navigates.
// The pair has to stay legible at the card's width, which a single-action
// story cannot show.
export const DownWithActionAndRemediation: Story = {
  args: {
    icon: Database,
    label: 'Providers',
    description:
      'Whether configured LLM providers are serving. Never blocks readiness.',
    state: 'down',
    action: (
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" size="sm" onClick={() => undefined}>
          Recheck now
        </Button>
        <Button variant="outline" size="sm" asChild>
          <Link to="/providers">Review providers</Link>
        </Button>
      </div>
    ),
  },
}

export const Loading: Story = {
  args: {
    ...Default.args,
    state: 'loading',
  },
}

export const Empty: Story = {
  args: {
    ...Default.args,
    state: 'unknown',
  },
}
