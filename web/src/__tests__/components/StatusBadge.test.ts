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

  it('applies correct color classes for known status', () => {
    const wrapper = mount(StatusBadge, {
      props: { value: 'completed' },
    })
    const tag = wrapper.find('.p-tag')
    expect(tag.exists()).toBe(true)
    expect(tag.classes()).toContain('bg-green-600')
    expect(tag.classes()).toContain('text-green-100')
  })

  it('applies correct color classes for priority', () => {
    const wrapper = mount(StatusBadge, {
      props: { value: 'high', type: 'priority' },
    })
    const tag = wrapper.find('.p-tag')
    expect(tag.exists()).toBe(true)
    expect(tag.classes()).toContain('bg-orange-600')
    expect(tag.classes()).toContain('text-orange-100')
  })

  it('falls back to slate for unknown value', () => {
    const wrapper = mount(StatusBadge, {
      props: { value: 'unknown_status' as never },
    })
    const tag = wrapper.find('.p-tag')
    expect(tag.exists()).toBe(true)
    expect(tag.classes()).toContain('bg-slate-600')
    expect(tag.classes()).toContain('text-slate-200')
  })
})
