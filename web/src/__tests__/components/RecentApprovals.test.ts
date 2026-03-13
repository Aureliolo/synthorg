import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import RecentApprovals from '@/components/dashboard/RecentApprovals.vue'
import type { ApprovalItem } from '@/api/types'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
}))

const mockApproval: ApprovalItem = {
  id: 'a1',
  action_type: 'tool_invoke',
  title: 'Deploy to production',
  description: 'Deploying latest build',
  requested_by: 'agent-1',
  risk_level: 'high',
  status: 'pending',
  task_id: 't1',
  metadata: {},
  decided_by: null,
  decision_reason: null,
  created_at: '2026-03-12T10:00:00Z',
  decided_at: null,
  expires_at: null,
}

describe('RecentApprovals', () => {
  it('renders "Recent Approvals" heading', () => {
    const wrapper = mount(RecentApprovals, {
      props: { approvals: [] },
    })
    expect(wrapper.text()).toContain('Recent Approvals')
  })

  it('shows "No recent approvals" when approvals array is empty', () => {
    const wrapper = mount(RecentApprovals, {
      props: { approvals: [] },
    })
    expect(wrapper.text()).toContain('No recent approvals')
  })

  it('renders approval titles when approvals are provided', () => {
    const approvals: ApprovalItem[] = [
      { ...mockApproval, id: 'a1', title: 'Deploy to production' },
      { ...mockApproval, id: 'a2', title: 'Access database' },
    ]
    const wrapper = mount(RecentApprovals, {
      props: { approvals },
    })
    expect(wrapper.text()).toContain('Deploy to production')
    expect(wrapper.text()).toContain('Access database')
  })

  it('renders at most 5 approval titles', () => {
    const approvals: ApprovalItem[] = Array.from({ length: 7 }, (_, i) => ({
      ...mockApproval,
      id: `a${i}`,
      title: `Approval ${i}`,
    }))
    const wrapper = mount(RecentApprovals, {
      props: { approvals },
    })
    expect(wrapper.text()).toContain('Approval 0')
    expect(wrapper.text()).toContain('Approval 4')
    expect(wrapper.text()).not.toContain('Approval 5')
    expect(wrapper.text()).not.toContain('Approval 6')
  })

  it('shows "View all" button', () => {
    const wrapper = mount(RecentApprovals, {
      props: { approvals: [] },
    })
    expect(wrapper.text()).toContain('View all')
  })

  it('navigates to /approvals when "View all" is clicked', async () => {
    const wrapper = mount(RecentApprovals, {
      props: { approvals: [] },
    })
    const link = wrapper.find('a')
    expect(link.attributes('href')).toBe('/approvals')
  })

  it('does not show "No recent approvals" when approvals are present', () => {
    const wrapper = mount(RecentApprovals, {
      props: { approvals: [mockApproval] },
    })
    expect(wrapper.text()).not.toContain('No recent approvals')
  })

  it('shows the requester name for each approval', () => {
    const wrapper = mount(RecentApprovals, {
      props: { approvals: [mockApproval] },
    })
    expect(wrapper.text()).toContain('agent-1')
  })
})
