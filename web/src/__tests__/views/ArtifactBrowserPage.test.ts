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

import ArtifactBrowserPage from '@/views/ArtifactBrowserPage.vue'

describe('ArtifactBrowserPage', () => {
  it('mounts without error', () => {
    const wrapper = mount(ArtifactBrowserPage)
    expect(wrapper.exists()).toBe(true)
  })

  it('renders "Artifacts" heading', () => {
    const wrapper = mount(ArtifactBrowserPage)
    expect(wrapper.find('h1').text()).toBe('Artifacts')
  })

  it('renders "Coming Soon" empty state', () => {
    const wrapper = mount(ArtifactBrowserPage)
    expect(wrapper.text()).toContain('Coming Soon')
  })

  it('renders GitHub issue link to #233', () => {
    const wrapper = mount(ArtifactBrowserPage)
    const link = wrapper.find('a[href*="github.com"]')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href')).toContain('/issues/233')
    expect(link.text()).toContain('#233')
  })

  it('opens GitHub link in new tab', () => {
    const wrapper = mount(ArtifactBrowserPage)
    const link = wrapper.find('a[href*="github.com"]')
    expect(link.attributes('target')).toBe('_blank')
    expect(link.attributes('rel')).toContain('noopener')
  })
})
