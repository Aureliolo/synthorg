import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import ChannelSelector from '@/components/messages/ChannelSelector.vue'
import type { Channel } from '@/api/types'

const DropdownStub = defineComponent({
  name: 'PvDropdown',
  props: ['modelValue', 'options', 'optionLabel', 'optionValue', 'placeholder', 'showClear'],
  emits: ['update:modelValue'],
  template: '<select data-testid="dropdown" />',
})

const mockChannels: Channel[] = [
  { name: 'general', type: 'broadcast', subscribers: ['agent-1', 'agent-2'] },
  { name: 'engineering', type: 'topic', subscribers: ['agent-1'] },
  { name: 'dm-a1-a2', type: 'direct', subscribers: ['agent-1', 'agent-2'] },
]

function mountSelector(props: { channels: Channel[]; modelValue: string | null }) {
  return mount(ChannelSelector, {
    props,
    global: {
      stubs: {
        Dropdown: DropdownStub,
      },
    },
  })
}

describe('ChannelSelector', () => {
  it('renders Dropdown with channels as options', () => {
    const wrapper = mountSelector({ channels: mockChannels, modelValue: null })

    const dropdown = wrapper.findComponent(DropdownStub)
    expect(dropdown.exists()).toBe(true)
    expect(dropdown.props('options')).toEqual(mockChannels)
  })

  it('uses name as option label and value', () => {
    const wrapper = mountSelector({ channels: mockChannels, modelValue: null })

    const dropdown = wrapper.findComponent(DropdownStub)
    expect(dropdown.props('optionLabel')).toBe('name')
    expect(dropdown.props('optionValue')).toBe('name')
  })

  it('passes modelValue to Dropdown', () => {
    const wrapper = mountSelector({ channels: mockChannels, modelValue: 'engineering' })

    const dropdown = wrapper.findComponent(DropdownStub)
    expect(dropdown.props('modelValue')).toBe('engineering')
  })

  it('emits update:modelValue when selection changes', async () => {
    const wrapper = mountSelector({ channels: mockChannels, modelValue: null })

    const dropdown = wrapper.findComponent(DropdownStub)
    await dropdown.vm.$emit('update:modelValue', 'general')

    expect(wrapper.emitted('update:modelValue')).toBeTruthy()
    expect(wrapper.emitted('update:modelValue')![0]).toEqual(['general'])
  })

  it('emits null when selection is cleared', async () => {
    const wrapper = mountSelector({ channels: mockChannels, modelValue: 'general' })

    const dropdown = wrapper.findComponent(DropdownStub)
    await dropdown.vm.$emit('update:modelValue', null)

    expect(wrapper.emitted('update:modelValue')).toBeTruthy()
    expect(wrapper.emitted('update:modelValue')![0]).toEqual([null])
  })

  it('shows placeholder text', () => {
    const wrapper = mountSelector({ channels: [], modelValue: null })

    const dropdown = wrapper.findComponent(DropdownStub)
    expect(dropdown.props('placeholder')).toBe('All Channels')
  })
})
