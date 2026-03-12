import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import StatusBadge from '@/components/common/StatusBadge.vue'

describe('StatusBadge', () => {
  it('renders status value as label', () => {
    const wrapper = mount(StatusBadge, {
      props: { value: 'in_progress' },
    })
    expect(wrapper.text()).toContain('In Progress')
  })

  it('renders priority type', () => {
    const wrapper = mount(StatusBadge, {
      props: { value: 'critical', type: 'priority' },
    })
    expect(wrapper.text()).toContain('Critical')
  })

  it('renders risk type', () => {
    const wrapper = mount(StatusBadge, {
      props: { value: 'high', type: 'risk' },
    })
    expect(wrapper.text()).toContain('High')
  })
})
