import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ApprovalCard from '@/components/approvals/ApprovalCard.vue'
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

describe('ApprovalCard', () => {
  it('renders approval title', () => {
    const wrapper = mount(ApprovalCard, { props: { approval: mockApproval } })
    expect(wrapper.text()).toContain('Deploy')
  })

  it('renders approval description', () => {
    const wrapper = mount(ApprovalCard, { props: { approval: mockApproval } })
    expect(wrapper.text()).toContain('Deploy v2')
  })

  it('renders requested_by and action_type', () => {
    const wrapper = mount(ApprovalCard, { props: { approval: mockApproval } })
    expect(wrapper.text()).toContain('agent-1')
    expect(wrapper.text()).toContain('deploy')
  })

  it('emits click event on click', async () => {
    const wrapper = mount(ApprovalCard, { props: { approval: mockApproval } })
    await wrapper.trigger('click')
    expect(wrapper.emitted('click')).toBeTruthy()
    expect(wrapper.emitted('click')![0]).toEqual([mockApproval])
  })

  it('emits click event on Enter keydown', async () => {
    const wrapper = mount(ApprovalCard, { props: { approval: mockApproval } })
    await wrapper.trigger('keydown.enter')
    expect(wrapper.emitted('click')).toBeTruthy()
    expect(wrapper.emitted('click')![0]).toEqual([mockApproval])
  })

  it('emits click event on Space keydown', async () => {
    const wrapper = mount(ApprovalCard, { props: { approval: mockApproval } })
    await wrapper.trigger('keydown.space')
    expect(wrapper.emitted('click')).toBeTruthy()
    expect(wrapper.emitted('click')![0]).toEqual([mockApproval])
  })

  it('has button role and tabindex for accessibility', () => {
    const wrapper = mount(ApprovalCard, { props: { approval: mockApproval } })
    const root = wrapper.find('[role="button"]')
    expect(root.exists()).toBe(true)
    expect(root.attributes('tabindex')).toBe('0')
  })

  it('renders with different approval data', () => {
    const other: ApprovalItem = {
      ...mockApproval,
      title: 'Scale Up',
      description: 'Add more agents',
      requested_by: 'agent-2',
      action_type: 'scale',
      risk_level: 'critical',
      status: 'approved',
    }
    const wrapper = mount(ApprovalCard, { props: { approval: other } })
    expect(wrapper.text()).toContain('Scale Up')
    expect(wrapper.text()).toContain('Add more agents')
    expect(wrapper.text()).toContain('agent-2')
    expect(wrapper.text()).toContain('scale')
  })
})
