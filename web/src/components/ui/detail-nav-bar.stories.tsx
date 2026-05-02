import type { Meta, StoryObj } from '@storybook/react'
import { fn } from 'storybook/test'
import { DetailNavBar } from './detail-nav-bar'

const meta = {
  title: 'Navigation/DetailNavBar',
  component: DetailNavBar,
  tags: ['autodocs'],
  parameters: { layout: 'padded' },
  args: {
    onPrev: fn(),
    onNext: fn(),
    bindShortcuts: false,
  },
  argTypes: {
    bindShortcuts: {
      control: 'boolean',
      description:
        'Bind global J / ArrowLeft (prev) and K / ArrowRight (next) shortcuts.',
    },
  },
} satisfies Meta<typeof DetailNavBar>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: {
    canPrev: true,
    canNext: true,
    position: { current: 7, total: 24 },
  },
}

export const FirstItem: Story = {
  args: {
    canPrev: false,
    canNext: true,
    position: { current: 1, total: 24 },
  },
}

export const LastItem: Story = {
  args: {
    canPrev: true,
    canNext: false,
    position: { current: 24, total: 24 },
  },
}

export const PositionHidden: Story = {
  args: {
    canPrev: true,
    canNext: true,
    position: null,
  },
  parameters: {
    docs: {
      description: {
        story:
          'When `position` is `null` (deep link / refresh without list context) the bar renders nothing. The component returns `null` and binds no keyboard listeners.',
      },
    },
  },
}

export const WithLabel: Story = {
  args: {
    canPrev: true,
    canNext: true,
    label: 'Phoenix Re-platform: kickoff document',
    position: { current: 12, total: 38 },
  },
}

export const ShortcutsBound: Story = {
  args: {
    canPrev: true,
    canNext: true,
    position: { current: 4, total: 10 },
    bindShortcuts: true,
  },
  parameters: {
    docs: {
      description: {
        story:
          'When `bindShortcuts` is `true` (the component default) the bar listens for J / ArrowLeft / K / ArrowRight on `window`. Inputs and contenteditable targets are excluded so typing in a form field does not advance the list.',
      },
    },
  },
}

export const BothDisabled: Story = {
  args: {
    canPrev: false,
    canNext: false,
    position: { current: 1, total: 1 },
  },
  parameters: {
    docs: {
      description: {
        story:
          'Single-item view: both buttons are disabled but the position counter still renders for context.',
      },
    },
  },
}
