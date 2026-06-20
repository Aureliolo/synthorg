import type { Meta, StoryObj } from '@storybook/react'

import type { AbTestRecord } from '@/api/endpoints/meta'
import { MetaABTestView } from './MetaABTestView'

const meta = {
  title: 'Pages/Meta/ABTestView',
  component: MetaABTestView,
  parameters: { a11y: { test: 'error' } },
} satisfies Meta<typeof MetaABTestView>

export default meta
type Story = StoryObj<typeof meta>

const baseTest: AbTestRecord = {
  id: '550e8400-e29b-41d4-a716-446655440000',
  name: 'Increase collaboration threshold',
  status: 'running',
  verdict: null,
  observation_hours_elapsed: 24,
  arms: [
    { name: 'control', agent_count: 10, fraction: 0.5 },
    { name: 'treatment', agent_count: 10, fraction: 0.5 },
  ],
  created_at: '2026-05-19T09:00:00Z',
  updated_at: '2026-05-19T10:00:00Z',
}

/** No active A/B tests. */
export const Empty: Story = {
  args: { tests: [] },
}

/** Active test in progress (no verdict yet). */
export const ActiveTest: Story = {
  args: { tests: [baseTest] },
}

/** Treatment declared winner. */
export const TreatmentWins: Story = {
  args: {
    tests: [
      {
        ...baseTest,
        status: 'completed',
        verdict: 'treatment_wins',
        observation_hours_elapsed: 48,
      },
    ],
  },
}

/** Inconclusive result. */
export const Inconclusive: Story = {
  args: {
    tests: [
      {
        ...baseTest,
        status: 'inconclusive',
        verdict: 'inconclusive',
        observation_hours_elapsed: 48,
      },
    ],
  },
}

/** Treatment regressed. */
export const TreatmentRegressed: Story = {
  args: {
    tests: [
      {
        ...baseTest,
        status: 'regressed',
        verdict: 'treatment_regressed',
        observation_hours_elapsed: 12,
      },
    ],
  },
}
