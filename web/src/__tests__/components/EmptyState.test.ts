import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import EmptyState from '@/components/common/EmptyState.vue'

describe('EmptyState', () => {
  it('renders title', () => {
    const wrapper = mount(EmptyState, {
      props: { title: 'No items found' },
    })
    expect(wrapper.text()).toContain('No items found')
  })

  it('renders message when provided', () => {
    const wrapper = mount(EmptyState, {
      props: { title: 'Empty', message: 'Nothing here yet' },
    })
    expect(wrapper.text()).toContain('Nothing here yet')
  })

  it('renders icon with correct class and aria-hidden', () => {
    const wrapper = mount(EmptyState, {
      props: { title: 'Empty', icon: 'pi pi-inbox' },
    })
    const icon = wrapper.find('i')
    expect(icon.exists()).toBe(true)
    expect(icon.classes()).toContain('pi')
    expect(icon.classes()).toContain('pi-inbox')
    expect(icon.attributes('aria-hidden')).toBe('true')
  })

  it('does not render message when not provided', () => {
    const wrapper = mount(EmptyState, {
      props: { title: 'Empty' },
    })
    const paragraphs = wrapper.findAll('p')
    expect(paragraphs).toHaveLength(0)
  })
})
