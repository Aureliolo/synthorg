import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import PageHeader from '@/components/common/PageHeader.vue'

describe('PageHeader', () => {
  it('renders title', () => {
    const wrapper = mount(PageHeader, {
      props: { title: 'Dashboard' },
    })
    expect(wrapper.find('h1').text()).toBe('Dashboard')
  })

  it('renders subtitle when provided', () => {
    const wrapper = mount(PageHeader, {
      props: { title: 'Dashboard', subtitle: 'Overview' },
    })
    expect(wrapper.text()).toContain('Overview')
  })

  it('does not render subtitle when not provided', () => {
    const wrapper = mount(PageHeader, {
      props: { title: 'Dashboard' },
    })
    const paragraphs = wrapper.findAll('p')
    expect(paragraphs).toHaveLength(0)
  })

  it('renders actions slot', () => {
    const wrapper = mount(PageHeader, {
      props: { title: 'Dashboard' },
      slots: { actions: '<button>Action</button>' },
    })
    expect(wrapper.find('button').exists()).toBe(true)
    expect(wrapper.find('button').text()).toBe('Action')
  })
})
