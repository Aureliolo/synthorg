import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import MetricCard from '@/components/dashboard/MetricCard.vue'

describe('MetricCard', () => {
  it('renders title and value', () => {
    const wrapper = mount(MetricCard, {
      props: { title: 'Total Tasks', value: '42', icon: 'pi pi-check' },
    })
    expect(wrapper.text()).toContain('Total Tasks')
    expect(wrapper.text()).toContain('42')
  })

  it('renders subtitle when provided', () => {
    const wrapper = mount(MetricCard, {
      props: { title: 'Cost', value: '$10.00', icon: 'pi pi-dollar', subtitle: 'This month' },
    })
    expect(wrapper.text()).toContain('This month')
  })

  it('renders icon', () => {
    const wrapper = mount(MetricCard, {
      props: { title: 'Agents', value: '5', icon: 'pi pi-users' },
    })
    const icon = wrapper.find('i')
    expect(icon.exists()).toBe(true)
  })
})
