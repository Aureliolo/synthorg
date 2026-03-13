import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import TaskFilters from '@/components/tasks/TaskFilters.vue'
import type { TaskFilters as TaskFilterType } from '@/api/types'

const DropdownStub = defineComponent({
  name: 'PvDropdown',
  props: ['modelValue', 'options', 'optionLabel', 'optionValue', 'placeholder', 'showClear'],
  emits: ['update:modelValue'],
  template: '<select data-testid="dropdown" />',
})

const ButtonStub = defineComponent({
  name: 'PvButton',
  props: ['label', 'icon', 'text', 'size'],
  emits: ['click'],
  template: '<button @click="$emit(\'click\')">{{ label }}</button>',
})

const defaultFilters: TaskFilterType = {}

function mountFilters(props: { agents: string[]; filters: TaskFilterType }) {
  return mount(TaskFilters, {
    props,
    global: {
      stubs: {
        Dropdown: DropdownStub,
        Button: ButtonStub,
      },
    },
  })
}

describe('TaskFilters', () => {
  it('renders status and agent dropdowns', () => {
    const wrapper = mountFilters({
      agents: ['agent-1', 'agent-2'],
      filters: defaultFilters,
    })

    const dropdowns = wrapper.findAllComponents(DropdownStub)
    expect(dropdowns).toHaveLength(2)

    // First dropdown is status, second is assignee
    expect(dropdowns[0].props('placeholder')).toBe('Status')
    expect(dropdowns[1].props('placeholder')).toBe('Assignee')
  })

  it('passes agents list to assignee dropdown', () => {
    const agents = ['agent-alpha', 'agent-beta']
    const wrapper = mountFilters({ agents, filters: defaultFilters })

    const dropdowns = wrapper.findAllComponents(DropdownStub)
    expect(dropdowns[1].props('options')).toEqual(agents)
  })

  it('passes current filter values to dropdowns', () => {
    const filters: TaskFilterType = { status: 'in_progress', assigned_to: 'agent-1' }
    const wrapper = mountFilters({ agents: ['agent-1'], filters })

    const dropdowns = wrapper.findAllComponents(DropdownStub)
    expect(dropdowns[0].props('modelValue')).toBe('in_progress')
    expect(dropdowns[1].props('modelValue')).toBe('agent-1')
  })

  it('emits reset when clear button is clicked', async () => {
    const wrapper = mountFilters({
      agents: ['agent-1'],
      filters: defaultFilters,
    })

    const button = wrapper.findComponent(ButtonStub)
    await button.trigger('click')

    expect(wrapper.emitted('reset')).toBeTruthy()
    expect(wrapper.emitted('reset')).toHaveLength(1)
  })

  it('emits update when status dropdown changes', async () => {
    const wrapper = mountFilters({
      agents: ['agent-1'],
      filters: defaultFilters,
    })

    const dropdowns = wrapper.findAllComponents(DropdownStub)
    await dropdowns[0].vm.$emit('update:modelValue', 'completed')

    expect(wrapper.emitted('update')).toBeTruthy()
    expect(wrapper.emitted('update')![0]).toEqual([{ status: 'completed' }])
  })

  it('emits update with undefined for falsy value (clearing a filter)', async () => {
    const wrapper = mountFilters({
      agents: ['agent-1'],
      filters: { status: 'completed' },
    })

    const dropdowns = wrapper.findAllComponents(DropdownStub)
    await dropdowns[0].vm.$emit('update:modelValue', null)

    expect(wrapper.emitted('update')).toBeTruthy()
    expect(wrapper.emitted('update')![0]).toEqual([{ status: undefined }])
  })
})
