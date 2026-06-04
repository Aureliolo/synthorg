import type { Meta, StoryObj } from '@storybook/react'

import { ParetoSection } from './ParetoSection'
import type { ParetoFrontier } from '@/api/types'

const stubFrontier: ParetoFrontier = {
  points: [
    {
      role_id: 'role-1',
      role_label: 'Backend Engineer',
      current_model: 'example-large-001',
      candidate_model: 'example-medium-001',
      quality_delta_pct: 7,
      cost_saving_pct: 70,
      source: 'stub:calibrated-v1',
    },
    {
      role_id: 'role-2',
      role_label: 'QA Engineer',
      current_model: 'example-medium-001',
      candidate_model: 'example-small-001',
      quality_delta_pct: 13,
      cost_saving_pct: 83,
      source: 'stub:calibrated-v1',
    },
  ],
  generated_at: '2026-05-20T12:00:00Z',
  baseline_window_size: 50,
  source: 'stub:calibrated-v1',
}

const measuredFrontier: ParetoFrontier = {
  ...stubFrontier,
  source: 'benchmark:1980-v1',
  points: stubFrontier.points.map((point) => ({
    ...point,
    source: 'benchmark:1980-v1',
  })),
}

// A frontier blending measured and stub rows: the aggregate source
// carries both tokens, so the badge resolves to 'mixed'.
const mixedFrontier: ParetoFrontier = {
  ...stubFrontier,
  source: 'benchmark:1980-v1, stub:calibrated-v1',
  points: stubFrontier.points.map((point, index) => ({
    ...point,
    source: index === 0 ? 'benchmark:1980-v1' : 'stub:calibrated-v1',
  })),
}

const meta = {
  title: 'Pages/Budget/ParetoSection',
  component: ParetoSection,
} satisfies Meta<typeof ParetoSection>

export default meta

type Story = StoryObj<typeof meta>

export const StubData: Story = {
  args: { frontier: stubFrontier },
}

export const MeasuredData: Story = {
  args: { frontier: measuredFrontier },
}

export const MixedData: Story = {
  args: { frontier: mixedFrontier },
}

export const EmptyState: Story = {
  args: { frontier: { ...stubFrontier, points: [] } },
}

export const Loading: Story = {
  args: { frontier: null, loading: true },
}

export const Unwired: Story = {
  args: { frontier: null },
}
