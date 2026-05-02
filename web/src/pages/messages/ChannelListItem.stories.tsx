import type { Meta, StoryObj } from '@storybook/react'
import { fn } from 'storybook/test'
import { ChannelListItem } from './ChannelListItem'

const meta: Meta<typeof ChannelListItem> = {
  title: 'Pages/Messages/ChannelListItem',
  component: ChannelListItem,
  parameters: { a11y: { test: 'error' } },
  args: { onSelect: fn() },
  decorators: [(Story) => <div className="w-56"><Story /></div>],
}
export default meta

type Story = StoryObj<typeof ChannelListItem>

export const TopicChannel: Story = {
  args: {
    channel: { name: '#engineering', type: 'topic', subscribers: [] },
    active: false,
    unreadCount: 0,
  },
}

export const DirectChannel: Story = {
  args: {
    channel: { name: '#dm-alice', type: 'direct', subscribers: [] },
    active: false,
    unreadCount: 0,
  },
}

export const BroadcastChannel: Story = {
  args: {
    channel: { name: '#all-hands', type: 'broadcast', subscribers: [] },
    active: false,
    unreadCount: 0,
  },
}

export const Active: Story = {
  args: {
    channel: { name: '#engineering', type: 'topic', subscribers: [] },
    active: true,
    unreadCount: 0,
  },
}

export const WithUnread: Story = {
  args: {
    channel: { name: '#product', type: 'topic', subscribers: [] },
    active: false,
    unreadCount: 12,
  },
}

export const ActiveWithUnread: Story = {
  args: {
    channel: { name: '#engineering', type: 'topic', subscribers: [] },
    active: true,
    unreadCount: 3,
  },
}
