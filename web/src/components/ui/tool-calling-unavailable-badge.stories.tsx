import type { Meta, StoryObj } from '@storybook/react-vite'
import { ToolCallingUnavailableBadge } from './tool-calling-unavailable-badge'

const meta = {
  title: 'UI/ToolCallingUnavailableBadge',
  component: ToolCallingUnavailableBadge,
  parameters: { layout: 'centered' },
} satisfies Meta<typeof ToolCallingUnavailableBadge>

export default meta
type Story = StoryObj<typeof meta>

export const Unavailable: Story = { args: { toolCallsVerified: false } }

export const Proven: Story = { args: { toolCallsVerified: true } }

export const Unobserved: Story = { args: { toolCallsVerified: null } }
