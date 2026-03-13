import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), go: vi.fn() }),
  useRoute: () => ({ params: {} }),
  RouterLink: { template: '<a><slot /></a>' },
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

vi.mock('@/components/common/EmptyState.vue', () => ({
  default: {
    props: ['icon', 'title', 'message'],
    template: '<div><span>{{ title }}</span><p>{{ message }}</p><slot name="action" /></div>',
  },
}))

import MeetingLogsPage from '@/views/MeetingLogsPage.vue'

describe('MeetingLogsPage', () => {
  it('mounts without error', () => {
    const wrapper = mount(MeetingLogsPage)
    expect(wrapper.exists()).toBe(true)
  })

  it('renders "Meeting Logs" heading', () => {
    const wrapper = mount(MeetingLogsPage)
    expect(wrapper.find('h1').text()).toBe('Meeting Logs')
  })

  it('renders "Coming Soon" empty state', () => {
    const wrapper = mount(MeetingLogsPage)
    expect(wrapper.text()).toContain('Coming Soon')
  })

  it('renders GitHub issue link to #264', () => {
    const wrapper = mount(MeetingLogsPage)
    const link = wrapper.find('a[href*="github.com"]')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href')).toContain('/issues/264')
    expect(link.text()).toContain('#264')
  })

  it('opens GitHub link in new tab', () => {
    const wrapper = mount(MeetingLogsPage)
    const link = wrapper.find('a[href*="github.com"]')
    expect(link.attributes('target')).toBe('_blank')
    expect(link.attributes('rel')).toContain('noopener')
  })
})
