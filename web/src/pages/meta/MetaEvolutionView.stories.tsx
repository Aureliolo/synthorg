import type { Meta, StoryObj } from '@storybook/react'

import type { EvolutionSummary } from '@/api/endpoints/meta'
import { MetaEvolutionView } from './MetaEvolutionView'

const meta = {
  title: 'Pages/Meta/EvolutionView',
  component: MetaEvolutionView,
  parameters: { a11y: { test: 'error' } },
} satisfies Meta<typeof MetaEvolutionView>

export default meta
type Story = StoryObj<typeof meta>

const summary: EvolutionSummary = {
  total_proposals: 12,
  approval_rate: 0.75,
  most_adapted_axis: 'identity',
  recent_outcomes: [
    {
      agent_id: 'agent-ceo',
      axis: 'identity',
      applied: true,
      proposed_at: '2026-05-19T09:00:00Z',
    },
    {
      agent_id: 'agent-cfo',
      axis: 'prompt_template',
      applied: false,
      proposed_at: '2026-05-19T08:30:00Z',
    },
  ],
}

const axes = [
  { axis: 'identity', count: 7 },
  { axis: 'prompt_template', count: 3 },
  { axis: 'strategy_selection', count: 2 },
]

/** No evolution outcomes recorded yet. */
export const Empty: Story = {
  args: { summary: null, axes: [] },
}

/** Populated dashboard with axis stats and recent outcomes. */
export const Populated: Story = {
  args: { summary, axes },
}
