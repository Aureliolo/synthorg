import type { Meta, StoryObj } from '@storybook/react-vite'
import { PackApplyPreviewDialog } from './PackApplyPreviewDialog'
import type { PackInfoResponse } from '@/api/types/templates'
import type { Department } from '@/api/types/org'

const samplePack: PackInfoResponse = {
  name: 'platform-team',
  display_name: 'Platform Team',
  description: 'Core platform engineers and SREs.',
  source: 'builtin',
  tags: ['engineering'],
  agent_count: 4,
  department_count: 1,
}

const fittingDepartments: readonly Department[] = [
  { name: 'engineering', display_name: 'Engineering', budget_percent: 50, teams: [] },
  { name: 'product', display_name: 'Product', budget_percent: 30, teams: [] },
]

const overflowDepartments: readonly Department[] = [
  { name: 'engineering', display_name: 'Engineering', budget_percent: 70, teams: [] },
  { name: 'product', display_name: 'Product', budget_percent: 25, teams: [] },
]

const meta = {
  title: 'OrgEdit/PackApplyPreviewDialog',
  component: PackApplyPreviewDialog,
  parameters: {
    a11y: { test: 'error' },
  },
  args: {
    open: true,
    onOpenChange: () => {},
    pack: samplePack,
    onApply: async () => {},
    applying: false,
  },
} satisfies Meta<typeof PackApplyPreviewDialog>

export default meta
type Story = StoryObj<typeof meta>

export const FitsBudget: Story = {
  args: { currentDepartments: fittingDepartments },
}

export const OverflowsBudget: Story = {
  args: { currentDepartments: overflowDepartments },
}

export const Applying: Story = {
  args: { currentDepartments: overflowDepartments, applying: true },
}
