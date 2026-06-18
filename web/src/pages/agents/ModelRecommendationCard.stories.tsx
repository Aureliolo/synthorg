import type { Meta, StoryObj } from '@storybook/react-vite'
import { fn } from 'storybook/test'
import { ModelRecommendationCard } from './ModelRecommendationCard'
import { buildRecommendation } from '@/mocks/handlers/recommendations'

const meta = {
  title: 'Agents/ModelRecommendationCard',
  component: ModelRecommendationCard,
  parameters: { layout: 'padded' },
  args: { onApprove: fn(), onReject: fn(), deciding: false },
} satisfies Meta<typeof ModelRecommendationCard>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: { recommendation: buildRecommendation() },
}

export const SingleAgent: Story = {
  args: { recommendation: buildRecommendation({ agent_ids: ['agent-1'] }) },
}

export const Deciding: Story = {
  args: { recommendation: buildRecommendation(), deciding: true },
}
