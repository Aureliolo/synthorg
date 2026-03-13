import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import OrgNode from '@/components/org-chart/OrgNode.vue'

describe('OrgNode', () => {
  it('renders label text', () => {
    const wrapper = mount(OrgNode, {
      props: { data: { label: 'Engineering', type: 'department' as const } },
    })
    expect(wrapper.text()).toContain('Engineering')
  })

  it('applies department styling when type is department', () => {
    const wrapper = mount(OrgNode, {
      props: { data: { label: 'Engineering', type: 'department' as const } },
    })
    const root = wrapper.find('div')
    expect(root.classes()).toContain('border-brand-600')
    expect(root.classes()).toContain('bg-brand-600/10')
  })

  it('applies team styling when type is team', () => {
    const wrapper = mount(OrgNode, {
      props: { data: { label: 'Backend Team', type: 'team' as const } },
    })
    const root = wrapper.find('div')
    expect(root.classes()).toContain('border-purple-600')
    expect(root.classes()).toContain('bg-purple-600/10')
  })

  it('applies agent styling when type is agent', () => {
    const wrapper = mount(OrgNode, {
      props: { data: { label: 'test-agent', type: 'agent' as const } },
    })
    const root = wrapper.find('div')
    expect(root.classes()).toContain('border-slate-700')
    expect(root.classes()).toContain('bg-slate-800')
  })

  it('renders role when provided', () => {
    const wrapper = mount(OrgNode, {
      props: { data: { label: 'test-agent', type: 'agent' as const, role: 'Developer' } },
    })
    expect(wrapper.text()).toContain('Developer')
  })

  it('does not render role text when not provided', () => {
    const wrapper = mount(OrgNode, {
      props: { data: { label: 'test-agent', type: 'agent' as const } },
    })
    // Only the label should be rendered as meaningful text
    const paragraphs = wrapper.findAll('p')
    expect(paragraphs).toHaveLength(1)
    expect(paragraphs[0].text()).toBe('test-agent')
  })

  it('renders formatted level when provided', () => {
    const wrapper = mount(OrgNode, {
      props: { data: { label: 'test-agent', type: 'agent' as const, level: 'senior' } },
    })
    expect(wrapper.text()).toContain('Senior')
  })

  it('does not render level when not provided', () => {
    const wrapper = mount(OrgNode, {
      props: { data: { label: 'test-agent', type: 'agent' as const, role: 'Dev' } },
    })
    // Should have label + role paragraphs, but no level paragraph
    const paragraphs = wrapper.findAll('p')
    expect(paragraphs).toHaveLength(2)
  })

  it('renders status badge when status is provided', () => {
    const wrapper = mount(OrgNode, {
      props: { data: { label: 'test-agent', type: 'agent' as const, status: 'active' } },
    })
    expect(wrapper.text()).toContain('Active')
  })

  it('does not render status badge when status is not provided', () => {
    const wrapper = mount(OrgNode, {
      props: { data: { label: 'test-agent', type: 'agent' as const } },
    })
    // StatusBadge renders a Tag with .p-tag class
    expect(wrapper.find('.p-tag').exists()).toBe(false)
  })

  it('shows department type badge for department nodes', () => {
    const wrapper = mount(OrgNode, {
      props: { data: { label: 'Engineering', type: 'department' as const } },
    })
    expect(wrapper.text()).toContain('Department')
  })

  it('does not show department type badge for non-department nodes', () => {
    const wrapper = mount(OrgNode, {
      props: { data: { label: 'Backend Team', type: 'team' as const } },
    })
    // Should not contain the "Department" type label span
    // The department badge specifically renders formatLabel(data.type) only for department type
    expect(wrapper.text()).not.toContain('Department')
  })

  it('renders all optional props together', () => {
    const wrapper = mount(OrgNode, {
      props: {
        data: {
          label: 'test-agent',
          type: 'agent' as const,
          role: 'Architect',
          level: 'principal',
          status: 'active',
        },
      },
    })
    expect(wrapper.text()).toContain('test-agent')
    expect(wrapper.text()).toContain('Architect')
    expect(wrapper.text()).toContain('Principal')
    expect(wrapper.text()).toContain('Active')
  })
})
