import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), go: vi.fn() }),
  useRoute: () => ({ params: {} }),
  RouterLink: { template: '<a><slot /></a>' },
  createRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    go: vi.fn(),
    beforeEach: vi.fn(),
    currentRoute: { value: { path: '/' } },
  }),
  createWebHistory: vi.fn(),
}))

vi.mock('@/components/layout/AppShell.vue', () => ({
  default: { template: '<div><slot /></div>' },
}))

vi.mock('@/components/common/PageHeader.vue', () => ({
  default: {
    props: ['title', 'subtitle'],
    template: '<div><h1>{{ title }}</h1><p>{{ subtitle }}</p><slot name="actions" /></div>',
  },
}))

vi.mock('@/components/common/LoadingSkeleton.vue', () => ({
  default: {
    props: ['lines'],
    template: '<div data-testid="loading-skeleton">Loading...</div>',
  },
}))

vi.mock('@/components/common/ErrorBoundary.vue', () => ({
  default: {
    props: ['error'],
    template: '<div><slot /></div>',
  },
}))

vi.mock('@/components/common/EmptyState.vue', () => ({
  default: {
    props: ['icon', 'title', 'message'],
    template: '<div data-testid="empty-state">{{ title }}</div>',
  },
}))

vi.mock('@/components/messages/MessageList.vue', () => ({
  default: {
    props: ['messages'],
    template: '<div data-testid="message-list">Messages</div>',
  },
}))

vi.mock('@/components/messages/ChannelSelector.vue', () => ({
  default: {
    props: ['modelValue', 'channels'],
    template: '<div data-testid="channel-selector">Channel Selector</div>',
  },
}))

vi.mock('@/api/endpoints/messages', () => ({
  listMessages: vi.fn().mockResolvedValue({ data: [], total: 0, offset: 0, limit: 100 }),
  listChannels: vi.fn().mockResolvedValue([]),
}))

vi.mock('@/api/endpoints/auth', () => ({
  getMe: vi.fn(),
  login: vi.fn(),
  setup: vi.fn(),
  changePassword: vi.fn(),
}))

import MessageFeedPage from '@/views/MessageFeedPage.vue'

describe('MessageFeedPage', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('mounts without error', () => {
    const wrapper = mount(MessageFeedPage)
    expect(wrapper.exists()).toBe(true)
  })

  it('renders "Messages" heading', () => {
    const wrapper = mount(MessageFeedPage)
    expect(wrapper.find('h1').text()).toBe('Messages')
  })

  it('fetches channels and messages on mount', async () => {
    const { listMessages, listChannels } = await import('@/api/endpoints/messages')
    mount(MessageFeedPage)
    await flushPromises()
    expect(listChannels).toHaveBeenCalled()
    expect(listMessages).toHaveBeenCalled()
  })

  it('shows empty state when no messages', async () => {
    const wrapper = mount(MessageFeedPage)
    await flushPromises()
    expect(wrapper.find('[data-testid="empty-state"]').exists()).toBe(true)
  })
})
