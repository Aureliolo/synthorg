import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import MessageList from '@/components/messages/MessageList.vue'
import type { Message } from '@/api/types'

const MessageItemStub = defineComponent({
  name: 'MessageItem',
  props: ['message'],
  template: '<div class="message-item">{{ message.content }}</div>',
})

function createMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: 'msg-1',
    timestamp: '2026-03-12T10:00:00Z',
    sender: 'agent-1',
    to: 'agent-2',
    type: 'task_update',
    priority: 'normal',
    channel: 'general',
    content: 'Task completed',
    attachments: [],
    metadata: { task_id: null, project_id: null, tokens_used: null, cost_usd: null, extra: [] },
    ...overrides,
  }
}

function mountList(messages: Message[]) {
  return mount(MessageList, {
    props: { messages },
    global: {
      stubs: {
        MessageItem: MessageItemStub,
      },
    },
  })
}

describe('MessageList', () => {
  it('renders a list of messages', () => {
    const messages = [
      createMessage({ id: 'msg-1', content: 'First message' }),
      createMessage({ id: 'msg-2', content: 'Second message' }),
      createMessage({ id: 'msg-3', content: 'Third message' }),
    ]

    const wrapper = mountList(messages)

    const items = wrapper.findAllComponents(MessageItemStub)
    expect(items).toHaveLength(3)
  })

  it('passes each message to MessageItem', () => {
    const messages = [
      createMessage({ id: 'msg-1', content: 'Hello' }),
      createMessage({ id: 'msg-2', content: 'World' }),
    ]

    const wrapper = mountList(messages)

    const items = wrapper.findAllComponents(MessageItemStub)
    expect(items[0].props('message')).toEqual(messages[0])
    expect(items[1].props('message')).toEqual(messages[1])
  })

  it('renders empty state when no messages', () => {
    const wrapper = mountList([])

    const items = wrapper.findAllComponents(MessageItemStub)
    expect(items).toHaveLength(0)
  })

  it('has scrollable container', () => {
    const wrapper = mountList([])

    const container = wrapper.find('.overflow-y-auto')
    expect(container.exists()).toBe(true)
  })
})
