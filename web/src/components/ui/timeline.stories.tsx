import type { Meta, StoryObj } from '@storybook/react'

import { Timeline, type TimelineFrame } from './timeline'

const meta = {
  title: 'UI/Timeline',
  component: Timeline,
  tags: ['autodocs'],
} satisfies Meta<typeof Timeline>

export default meta
type Story = StoryObj<typeof meta>

const FRAMES: readonly TimelineFrame[] = [
  { turnIndex: 1, status: 'in_progress' },
  { turnIndex: 2, status: 'in_progress' },
  { turnIndex: 3, status: 'blocked' },
  { turnIndex: 4, status: 'in_progress' },
  { turnIndex: 5, status: 'completed' },
]

export const Default: Story = {
  args: { frames: FRAMES, currentIndex: 2, onSeek: () => {} },
}

export const AtStart: Story = {
  args: { frames: FRAMES, currentIndex: 0, onSeek: () => {} },
}

export const Failed: Story = {
  args: {
    frames: [
      { turnIndex: 1, status: 'in_progress' },
      { turnIndex: 2, status: 'failed' },
    ],
    currentIndex: 1,
    onSeek: () => {},
  },
}

export const Empty: Story = {
  args: { frames: [], currentIndex: 0, onSeek: () => {} },
}

// A non-default `label` sets the scrubber's accessible name; required when
// several timelines share a page so each has a distinct aria-label.
export const CustomLabel: Story = {
  args: {
    frames: FRAMES,
    currentIndex: 2,
    onSeek: () => {},
    label: 'Flight recorder scrubber',
  },
}
