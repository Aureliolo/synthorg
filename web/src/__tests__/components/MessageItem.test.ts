import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import MessageItem from '@/components/messages/MessageItem.vue'
import type { Message } from '@/api/types'

const mockMessage: Message = {
  id: 'm1',
  sender: 'agent-1',
  to: 'agent-2',
  content: 'Hello team',
  channel: 'general',
  type: 'task_update',
  priority: 'normal',
  timestamp: '2026-03-12T10:00:00Z',
  attachments: [],
  metadata: {
    task_id: null,
    project_id: null,
    tokens_used: null,
    cost_usd: null,
    extra: [],
  },
}

describe('MessageItem', () => {
  it('renders the sender name', () => {
    const wrapper = mount(MessageItem, {
      props: { message: mockMessage },
    })
    expect(wrapper.text()).toContain('agent-1')
  })

  it('renders the message content', () => {
    const wrapper = mount(MessageItem, {
      props: { message: mockMessage },
    })
    expect(wrapper.text()).toContain('Hello team')
  })

  it('renders the channel name', () => {
    const wrapper = mount(MessageItem, {
      props: { message: mockMessage },
    })
    expect(wrapper.text()).toContain('general')
  })

  it('renders content with whitespace preserved', () => {
    const multiline: Message = {
      ...mockMessage,
      content: 'Line one\nLine two',
    }
    const wrapper = mount(MessageItem, {
      props: { message: multiline },
    })
    const contentEl = wrapper.find('.whitespace-pre-wrap')
    expect(contentEl.exists()).toBe(true)
    expect(contentEl.text()).toContain('Line one')
    expect(contentEl.text()).toContain('Line two')
  })

  it('renders with different sender and channel values', () => {
    const custom: Message = {
      ...mockMessage,
      sender: 'ceo-bot',
      channel: 'engineering',
    }
    const wrapper = mount(MessageItem, {
      props: { message: custom },
    })
    expect(wrapper.text()).toContain('ceo-bot')
    expect(wrapper.text()).toContain('engineering')
  })

  it('renders empty content gracefully', () => {
    const empty: Message = { ...mockMessage, content: '' }
    const wrapper = mount(MessageItem, {
      props: { message: empty },
    })
    expect(wrapper.text()).toContain('agent-1')
    expect(wrapper.text()).toContain('general')
  })
})
