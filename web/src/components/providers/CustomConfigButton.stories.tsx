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
  args: { onClick: () => alert('Configure manually clicked') },
}
