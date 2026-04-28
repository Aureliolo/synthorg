import type { Meta, StoryObj } from '@storybook/react-vite'
import { MemoryRouter } from 'react-router'
import CustomRulesPage from './CustomRulesPage'
import { useCustomRulesStore } from '@/stores/custom-rules'
import type { CustomRule } from '@/api/endpoints/custom-rules'

const sampleRules: readonly CustomRule[] = [
  {
    id: 'rule-1',
    name: 'budget-spike',
    description: 'Pause non-critical agents when daily spend exceeds 1.5x average.',
    metric_path: 'budget.daily_spend',
    comparator: 'gt',
    threshold: 1.5,
    severity: 'warning',
    target_altitudes: ['config_tuning'],
    enabled: true,
    created_at: '2026-04-20T10:00:00+00:00',
    updated_at: '2026-04-25T08:00:00+00:00',
  },
  {
    id: 'rule-2',
    name: 'p99-latency',
    description: 'Page on persistent p99 above 2s.',
    metric_path: 'engine.p99_latency_ms',
    comparator: 'gt',
    threshold: 2000,
    severity: 'critical',
    target_altitudes: ['architecture'],
    enabled: false,
    created_at: '2026-04-22T11:00:00+00:00',
    updated_at: '2026-04-22T11:00:00+00:00',
  },
]

const meta = {
  title: 'Pages/CustomRulesPage',
  component: CustomRulesPage,
  parameters: { layout: 'fullscreen' },
  decorators: [
    (Story) => {
      useCustomRulesStore.setState({
        rules: sampleRules,
        loading: false,
        error: null,
      })
      return (
        <MemoryRouter>
          <Story />
        </MemoryRouter>
      )
    },
  ],
} satisfies Meta<typeof CustomRulesPage>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}

export const Loading: Story = {
  decorators: [
    (Story) => {
      useCustomRulesStore.setState({ rules: [], loading: true, error: null })
      return (
        <MemoryRouter>
          <Story />
        </MemoryRouter>
      )
    },
  ],
}

export const Empty: Story = {
  decorators: [
    (Story) => {
      useCustomRulesStore.setState({ rules: [], loading: false, error: null })
      return (
        <MemoryRouter>
          <Story />
        </MemoryRouter>
      )
    },
  ],
}

export const ErrorState: Story = {
  decorators: [
    (Story) => {
      useCustomRulesStore.setState({
        rules: [],
        loading: false,
        error: 'Backend unreachable',
      })
      return (
        <MemoryRouter>
          <Story />
        </MemoryRouter>
      )
    },
  ],
}
