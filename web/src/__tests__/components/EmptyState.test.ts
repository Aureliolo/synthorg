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

  it('renders action slot when provided', () => {
    const wrapper = mount(EmptyState, {
      props: { title: 'Empty' },
      slots: { action: '<button>Create Item</button>' },
    })
    const button = wrapper.find('button')
    expect(button.exists()).toBe(true)
    expect(button.text()).toBe('Create Item')
  })

  it('does not render action container when slot is not provided', () => {
    const wrapper = mount(EmptyState, {
      props: { title: 'Empty' },
    })
    // The mt-4 div should not render when no action slot is provided
    const actionDivs = wrapper.findAll('.mt-4')
    expect(actionDivs).toHaveLength(0)
  })
})
