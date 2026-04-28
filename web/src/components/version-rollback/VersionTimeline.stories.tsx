import type { Meta, StoryObj } from '@storybook/react-vite'
import { VersionTimeline, type TimelineItem } from './VersionTimeline'

interface SampleSnapshot extends TimelineItem {
  readonly summary: string
}

const items: readonly SampleSnapshot[] = [
  {
    id: 'snap-3',
    version: 3,
    created_at: '2026-04-28T09:00:00+00:00',
    summary: 'Tightened persona rubric.',
  },
  {
    id: 'snap-2',
    version: 2,
    created_at: '2026-04-27T18:30:00+00:00',
    summary: 'Updated default escalation thresholds.',
  },
  {
    id: 'snap-1',
    version: 1,
    created_at: '2026-04-25T08:00:00+00:00',
    summary: 'Initial snapshot.',
  },
]

const meta = {
  title: 'VersionRollback/VersionTimeline',
  component: VersionTimeline<SampleSnapshot>,
  args: {
    items,
    loading: false,
    loadingMore: false,
    hasMore: false,
    selectedVersion: null,
    onSelect: () => {},
    onLoadMore: () => {},
  },
} satisfies Meta<typeof VersionTimeline<SampleSnapshot>>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}
export const SelectedVersion: Story = { args: { selectedVersion: 2 } }
export const Loading: Story = { args: { items: [], loading: true } }
export const Empty: Story = { args: { items: [], loading: false } }
export const HasMore: Story = { args: { hasMore: true } }
