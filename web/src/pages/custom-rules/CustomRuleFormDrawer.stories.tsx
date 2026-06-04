import type { Meta, StoryObj } from '@storybook/react-vite'
import { CustomRuleFormDrawer } from './CustomRuleFormDrawer'
import { useCustomRulesStore } from '@/stores/custom-rules'
import type { CustomRule } from '@/api/endpoints/custom-rules'

const sampleRule: CustomRule = {
  id: 'rule-1',
  name: 'High cost spike',
  description: 'Surface a tuning proposal when daily spend triples baseline.',
  metric_path: 'budget.cost.daily_avg',
  comparator: 'gt',
  threshold: 3.0,
  severity: 'warning',
  target_altitudes: ['config_tuning'],
  enabled: true,
  created_at: '2026-04-28T08:00:00+00:00',
  updated_at: '2026-04-28T08:00:00+00:00',
}

const meta = {
  title: 'CustomRules/CustomRuleFormDrawer',
  component: CustomRuleFormDrawer,
  args: {
    open: true,
    onClose: () => {},
  },
  decorators: [
    (Story) => {
      useCustomRulesStore.setState({
        submitting: false,
        createRule: () => Promise.resolve(sampleRule),
        updateRule: () => Promise.resolve(sampleRule),
      })
      return <Story />
    },
  ],
} satisfies Meta<typeof CustomRuleFormDrawer>

export default meta
type Story = StoryObj<typeof meta>

export const Create: Story = { args: { mode: 'create', rule: null } }
export const Edit: Story = { args: { mode: 'edit', rule: sampleRule } }
export const Closed: Story = { args: { mode: 'create', rule: null, open: false } }
