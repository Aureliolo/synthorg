import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), go: vi.fn() }),
  useRoute: () => ({ params: {}, query: {} }),
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

vi.mock('primevue/usetoast', () => ({
  useToast: () => ({ add: vi.fn() }),
}))

vi.mock('primevue/tabview', () => ({
  default: { template: '<div><slot /></div>' },
}))

vi.mock('primevue/tabpanel', () => ({
  default: {
    props: ['header', 'value'],
    template: '<div><slot /></div>',
  },
}))

vi.mock('primevue/inputtext', () => ({
  default: {
    props: ['modelValue', 'type', 'placeholder'],
    template: '<input :type="type" :placeholder="placeholder" />',
  },
}))

vi.mock('primevue/button', () => ({
  default: {
    props: ['label', 'icon', 'type', 'size', 'loading', 'disabled'],
    template: '<button :disabled="disabled">{{ label }}</button>',
  },
}))

vi.mock('primevue/datatable', () => ({
  default: {
    props: ['value', 'stripedRows'],
    template: '<div data-testid="datatable"><slot /></div>',
  },
}))

vi.mock('primevue/column', () => ({
  default: {
    props: ['field', 'header', 'sortable'],
    template: '<div><slot /></div>',
  },
}))

vi.mock('@/components/layout/AppShell.vue', () => ({
  default: { template: '<div><slot /></div>' },
}))

vi.mock('@/components/common/PageHeader.vue', () => ({
  default: {
    props: ['title', 'subtitle'],
    template: '<div><h1>{{ title }}</h1><p>{{ subtitle }}</p></div>',
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

vi.mock('@/api/endpoints/company', () => ({
  getCompanyConfig: vi.fn().mockResolvedValue({
    company_name: 'Test Corp',
    agents: [{ name: 'alice', role: 'Developer' }],
  }),
  listDepartments: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  getDepartment: vi.fn(),
}))

vi.mock('@/api/endpoints/providers', () => ({
  listProviders: vi.fn().mockResolvedValue({}),
  getProvider: vi.fn(),
  getProviderModels: vi.fn(),
}))

vi.mock('@/api/endpoints/auth', () => ({
  getMe: vi.fn(),
  login: vi.fn(),
  setup: vi.fn(),
  changePassword: vi.fn(),
}))

import SettingsPage from '@/views/SettingsPage.vue'

describe('SettingsPage', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('mounts without error', () => {
    const wrapper = mount(SettingsPage)
    expect(wrapper.exists()).toBe(true)
  })

  it('renders "Settings" heading', () => {
    const wrapper = mount(SettingsPage)
    expect(wrapper.find('h1').text()).toBe('Settings')
  })

  it('shows loading skeleton initially', () => {
    const wrapper = mount(SettingsPage)
    expect(wrapper.find('[data-testid="loading-skeleton"]').exists()).toBe(true)
  })

  it('fetches company config and providers on mount', async () => {
    const { getCompanyConfig } = await import('@/api/endpoints/company')
    const { listProviders } = await import('@/api/endpoints/providers')
    mount(SettingsPage)
    await flushPromises()
    expect(getCompanyConfig).toHaveBeenCalled()
    expect(listProviders).toHaveBeenCalled()
  })

  it('renders tabs after loading', async () => {
    const wrapper = mount(SettingsPage)
    await flushPromises()
    expect(wrapper.text()).toContain('Change Password')
  })
})
