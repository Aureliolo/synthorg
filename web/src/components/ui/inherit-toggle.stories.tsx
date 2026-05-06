import type { Meta, StoryObj } from '@storybook/react-vite'
import { InheritToggle } from './inherit-toggle'

const meta = {
  title: 'UI/InheritToggle',
  component: InheritToggle,
  tags: ['autodocs'],
} satisfies Meta<typeof InheritToggle>

export default meta
type Story = StoryObj<typeof meta>

export const Inherit: Story = {
  args: { inherit: true, onChange: () => {} },
}

export const Override: Story = {
  args: { inherit: false, onChange: () => {} },
}

export const Disabled: Story = {
  args: { inherit: true, onChange: () => {}, disabled: true },
}

export const InheritFromCustomSource: Story = {
  args: { inherit: true, onChange: () => {}, inheritFrom: 'company defaults' },
}
