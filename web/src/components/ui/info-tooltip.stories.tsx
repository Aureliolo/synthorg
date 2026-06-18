import type { Meta, StoryObj } from '@storybook/react-vite'
import { AlertTriangle, Info } from 'lucide-react'
import { InfoTooltip } from './info-tooltip'

const meta = {
  title: 'UI/InfoTooltip',
  component: InfoTooltip,
  parameters: { layout: 'centered' },
} satisfies Meta<typeof InfoTooltip>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: {
    content: 'This is a short, non-interactive explanation shown on hover.',
    children: <Info className="size-4 text-muted-foreground" aria-hidden="true" />,
  },
}

export const Warning: Story = {
  args: {
    content:
      'An earlier step changed since you completed this step. Re-visit it to confirm your selections are still valid.',
    children: <AlertTriangle className="size-4 text-warning" aria-hidden="true" />,
  },
}

export const RichContent: Story = {
  args: {
    content: (
      <div className="flex flex-col gap-1">
        <span className="font-semibold">Heads up</span>
        <span>Multiple lines of explanation render fine here.</span>
      </div>
    ),
    children: <Info className="size-4 text-accent" aria-hidden="true" />,
  },
}
