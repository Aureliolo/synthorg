import type { Meta, StoryObj } from '@storybook/react-vite'
import { ProviderLogo } from './ProviderLogo'

const meta = {
  title: 'Providers/ProviderLogo',
  component: ProviderLogo,
  parameters: { layout: 'centered' },
} satisfies Meta<typeof ProviderLogo>

export default meta
type Story = StoryObj<typeof meta>

export const Existing: Story = {
  args: { name: 'anthropic' },
  parameters: { docs: { description: { story: 'Renders provider logo when the SVG is present.' } } },
}

export const FallbackForMissing: Story = {
  args: { name: 'nonexistent-provider' },
  parameters: { docs: { description: { story: 'Renders the generic Server icon when no SVG is bundled for the preset.' } } },
}

export const Small: Story = {
  args: { name: 'openai', size: 16 },
}

export const Large: Story = {
  args: { name: 'gemini', size: 48 },
}
