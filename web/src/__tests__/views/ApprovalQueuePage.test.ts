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

vi.mock('primevue/usetoast', () => ({
  useToast: () => ({ add: vi.fn() }),
}))

vi.mock('primevue/datatable', () => ({
  default: {
    props: ['value', 'totalRecords', 'loading', 'stripedRows', 'rowHover'],
    template: '<div data-testid="datatable"><slot /></div>',
  },
}))

vi.mock('primevue/column', () => ({
  default: {
    props: ['field', 'header', 'sortable', 'style'],
    template: '<div><slot /></div>',
  },
}))

vi.mock('primevue/sidebar', () => ({
  default: {
    props: ['visible', 'position'],
    template: '<div data-testid="sidebar"><slot /><slot name="header" /></div>',
  },
}))

vi.mock('primevue/dropdown', () => ({
  default: {
    props: ['modelValue', 'options', 'optionLabel', 'optionValue', 'placeholder', 'showClear'],
    template: '<select data-testid="status-filter"></select>',
  },
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

vi.mock('@/components/common/StatusBadge.vue', () => ({
  default: {
    props: ['value', 'type'],
    template: '<span>{{ value }}</span>',
  },
}))

vi.mock('@/components/approvals/ApprovalDetail.vue', () => ({
  default: {
    props: ['approval'],
    template: '<div data-testid="approval-detail">Approval Detail</div>',
  },
}))

vi.mock('@/components/approvals/ApprovalActions.vue', () => ({
  default: {
    props: ['approvalId', 'status', 'loading'],
    template: '<div data-testid="approval-actions">Actions</div>',
  },
}))

vi.mock('@/api/endpoints/approvals', () => ({
  listApprovals: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  getApproval: vi.fn(),
  createApproval: vi.fn(),
  approveApproval: vi.fn(),
  rejectApproval: vi.fn(),
}))

vi.mock('@/api/endpoints/auth', () => ({
  getMe: vi.fn(),
  login: vi.fn(),
  setup: vi.fn(),
  changePassword: vi.fn(),
}))

import ApprovalQueuePage from '@/views/ApprovalQueuePage.vue'

describe('ApprovalQueuePage', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('mounts without error', () => {
    const wrapper = mount(ApprovalQueuePage)
    expect(wrapper.exists()).toBe(true)
  })

  it('renders "Approval Queue" heading', () => {
    const wrapper = mount(ApprovalQueuePage)
    expect(wrapper.find('h1').text()).toBe('Approval Queue')
  })

  it('fetches approvals on mount', async () => {
    const { listApprovals } = await import('@/api/endpoints/approvals')
    mount(ApprovalQueuePage)
    await flushPromises()
    expect(listApprovals).toHaveBeenCalled()
  })

  it('renders status filter dropdown', () => {
    const wrapper = mount(ApprovalQueuePage)
    expect(wrapper.find('[data-testid="status-filter"]').exists()).toBe(true)
  })

  it('renders data table for approvals', async () => {
    const wrapper = mount(ApprovalQueuePage)
    await flushPromises()
    expect(wrapper.find('[data-testid="datatable"]').exists()).toBe(true)
  })
})
