import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ApprovalDetail from '@/components/approvals/ApprovalDetail.vue'
import type { ApprovalItem } from '@/api/types'

const mockApproval: ApprovalItem = {
  id: 'a1',
  title: 'Deploy',
  description: 'Deploy v2',
  requested_by: 'agent-1',
  action_type: 'deploy',
  risk_level: 'high',
  status: 'pending',
  task_id: null,
  metadata: {},
  decided_by: null,
  decision_reason: null,
  created_at: '2026-03-12T10:00:00Z',
  decided_at: null,
  expires_at: '2026-03-12T11:00:00Z',
}

describe('ApprovalDetail', () => {
  it('renders approval title', () => {
    const wrapper = mount(ApprovalDetail, { props: { approval: mockApproval } })
    expect(wrapper.find('h3').text()).toBe('Deploy')
  })

  it('renders approval description', () => {
    const wrapper = mount(ApprovalDetail, { props: { approval: mockApproval } })
    expect(wrapper.text()).toContain('Deploy v2')
  })

  it('renders action type', () => {
    const wrapper = mount(ApprovalDetail, { props: { approval: mockApproval } })
    expect(wrapper.text()).toContain('deploy')
  })

  it('renders requested_by', () => {
    const wrapper = mount(ApprovalDetail, { props: { approval: mockApproval } })
    expect(wrapper.text()).toContain('agent-1')
  })

  it('renders created_at date', () => {
    const wrapper = mount(ApprovalDetail, { props: { approval: mockApproval } })
    // formatDate outputs locale string; just check it rendered something for Created
    expect(wrapper.text()).toContain('Created')
  })

  it('renders expires_at date', () => {
    const wrapper = mount(ApprovalDetail, { props: { approval: mockApproval } })
    expect(wrapper.text()).toContain('Expires')
  })

  it('does not render decided_by when null', () => {
    const wrapper = mount(ApprovalDetail, { props: { approval: mockApproval } })
    expect(wrapper.text()).not.toContain('Decided By')
  })

  it('renders decided_by when present', () => {
    const decided: ApprovalItem = {
      ...mockApproval,
      decided_by: 'admin-user',
      decided_at: '2026-03-12T10:30:00Z',
    }
    const wrapper = mount(ApprovalDetail, { props: { approval: decided } })
    expect(wrapper.text()).toContain('Decided By')
    expect(wrapper.text()).toContain('admin-user')
  })

  it('renders decided_at when present', () => {
    const decided: ApprovalItem = {
      ...mockApproval,
      decided_by: 'admin-user',
      decided_at: '2026-03-12T10:30:00Z',
    }
    const wrapper = mount(ApprovalDetail, { props: { approval: decided } })
    expect(wrapper.text()).toContain('Decided At')
  })

  it('does not render decision_reason when null', () => {
    const wrapper = mount(ApprovalDetail, { props: { approval: mockApproval } })
    expect(wrapper.text()).not.toContain('Decision Comment')
  })

  it('renders decision_reason when present', () => {
    const withReason: ApprovalItem = {
      ...mockApproval,
      decision_reason: 'Looks good to proceed',
    }
    const wrapper = mount(ApprovalDetail, { props: { approval: withReason } })
    expect(wrapper.text()).toContain('Decision Comment')
    expect(wrapper.text()).toContain('Looks good to proceed')
  })

  it('does not render metadata section when metadata is empty', () => {
    const wrapper = mount(ApprovalDetail, { props: { approval: mockApproval } })
    expect(wrapper.text()).not.toContain('Metadata')
  })

  it('renders metadata when non-empty', () => {
    const withMeta: ApprovalItem = {
      ...mockApproval,
      metadata: { environment: 'production', version: '2.0.0' },
    }
    const wrapper = mount(ApprovalDetail, { props: { approval: withMeta } })
    expect(wrapper.text()).toContain('Metadata')
    expect(wrapper.text()).toContain('environment')
    expect(wrapper.text()).toContain('production')
    expect(wrapper.text()).toContain('version')
    expect(wrapper.text()).toContain('2.0.0')
  })

  it('renders status and risk level labels', () => {
    const wrapper = mount(ApprovalDetail, { props: { approval: mockApproval } })
    expect(wrapper.text()).toContain('Status')
    expect(wrapper.text()).toContain('Risk Level')
  })
})
