import type { Meta, StoryObj } from '@storybook/react-vite'
import { CustomConfigButton } from './CustomConfigButton'

const meta = {
  title: 'Providers/CustomConfigButton',
  component: CustomConfigButton,
  parameters: { layout: 'centered' },
} satisfies Meta<typeof CustomConfigButton>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  // No-op callback: ``alert`` would block Storybook's test-runner and
  // visual-regression harnesses.
  args: { onClick: () => undefined },
}
