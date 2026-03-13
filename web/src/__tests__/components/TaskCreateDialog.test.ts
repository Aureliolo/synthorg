import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import { defineComponent, h, nextTick } from 'vue'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  RouterLink: { template: '<a><slot /></a>' },
}))

vi.mock('primevue/dialog', () => ({
  default: defineComponent({
    name: 'PvDialog',
    props: ['visible', 'header', 'modal'],
    emits: ['update:visible'],
    setup(props, { slots }) {
      return () =>
        props.visible
          ? h('div', { class: 'dialog' }, [slots.default?.(), slots.footer?.() ])
          : null
    },
  }),
}))

vi.mock('primevue/inputtext', () => ({
  default: defineComponent({
    props: ['modelValue', 'id', 'placeholder'],
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          id: props.id,
          value: props.modelValue,
          onInput: (e: Event) => emit('update:modelValue', (e.target as HTMLInputElement).value),
        })
    },
  }),
}))

vi.mock('primevue/textarea', () => ({
  default: defineComponent({
    props: ['modelValue', 'id', 'rows', 'placeholder'],
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('textarea', {
          id: props.id,
          value: props.modelValue,
          onInput: (e: Event) => emit('update:modelValue', (e.target as HTMLTextAreaElement).value),
        })
    },
  }),
}))

vi.mock('primevue/dropdown', () => ({
  default: defineComponent({
    props: ['modelValue', 'id', 'options', 'optionLabel', 'optionValue', 'placeholder', 'showClear'],
    emits: ['update:modelValue'],
    setup(props) {
      return () => h('select', { id: props.id }, String(props.modelValue))
    },
  }),
}))

vi.mock('primevue/inputnumber', () => ({
  default: defineComponent({
    props: ['modelValue', 'id', 'mode', 'currency', 'min'],
    emits: ['update:modelValue'],
    setup(props) {
      return () => h('input', { id: props.id, type: 'number', value: props.modelValue })
    },
  }),
}))

vi.mock('@/router', () => ({
  router: {
    currentRoute: { value: { path: '/tasks' } },
    push: vi.fn(),
  },
}))

vi.mock('primevue/button', () => ({
  default: defineComponent({
    props: ['label', 'icon', 'text', 'disabled'],
    emits: ['click'],
    setup(props, { emit }) {
      return () =>
        h('button', { disabled: props.disabled, onClick: () => emit('click') }, props.label)
    },
  }),
}))

import TaskCreateDialog from '@/components/tasks/TaskCreateDialog.vue'
import { useAuthStore } from '@/stores/auth'

describe('TaskCreateDialog', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    localStorage.clear()
    const auth = useAuthStore()
    auth.user = { id: 'u1', username: 'test-ceo', role: 'ceo', must_change_password: false }
  })

  function mountDialog(visible = true) {
    return mount(TaskCreateDialog, {
      props: { visible, agents: ['agent-1', 'agent-2'] },
    })
  }

  it('renders dialog with form when visible', () => {
    const wrapper = mountDialog()
    expect(wrapper.find('.dialog').exists()).toBe(true)
    expect(wrapper.find('#task-title').exists()).toBe(true)
    expect(wrapper.find('#task-description').exists()).toBe(true)
  })

  it('does not render dialog when not visible', () => {
    const wrapper = mountDialog(false)
    expect(wrapper.find('.dialog').exists()).toBe(false)
  })

  it('renders all form labels', () => {
    const wrapper = mountDialog()
    expect(wrapper.find('label[for="task-title"]').exists()).toBe(true)
    expect(wrapper.find('label[for="task-description"]').exists()).toBe(true)
    expect(wrapper.find('label[for="task-type"]').exists()).toBe(true)
    expect(wrapper.find('label[for="task-priority"]').exists()).toBe(true)
    expect(wrapper.find('label[for="task-project"]').exists()).toBe(true)
    expect(wrapper.find('label[for="task-assignee"]').exists()).toBe(true)
    expect(wrapper.find('label[for="task-complexity"]').exists()).toBe(true)
    expect(wrapper.find('label[for="task-budget"]').exists()).toBe(true)
  })

  it('shows logged-in user as read-only Created By', () => {
    const wrapper = mountDialog()
    expect(wrapper.text()).toContain('test-ceo')
  })

  it('has Create button disabled when required fields are empty', () => {
    const wrapper = mountDialog()
    const createBtn = wrapper.findAll('button').find((b) => b.text() === 'Create')
    expect(createBtn).toBeDefined()
    expect(createBtn!.attributes('disabled')).toBeDefined()
  })

  it('emits create with form data on valid submit', async () => {
    const wrapper = mountDialog()

    await wrapper.find('#task-title').setValue('My Task')
    await wrapper.find('#task-description').setValue('Task description')
    await wrapper.find('#task-project').setValue('proj-1')

    const createBtn = wrapper.findAll('button').find((b) => b.text() === 'Create')
    await createBtn!.trigger('click')

    const emitted = wrapper.emitted('create')
    expect(emitted).toBeTruthy()
    expect(emitted![0][0]).toMatchObject({
      title: 'My Task',
      description: 'Task description',
      project: 'proj-1',
      created_by: 'test-ceo',
      type: 'development',
      priority: 'medium',
    })
  })

  it('resets form when dialog becomes visible', async () => {
    const wrapper = mount(TaskCreateDialog, {
      props: { visible: false, agents: [] },
    })

    await wrapper.setProps({ visible: true })
    await nextTick()

    const titleInput = wrapper.find('#task-title')
    if (titleInput.exists()) {
      expect((titleInput.element as HTMLInputElement).value).toBe('')
    }
  })

  it('has Cancel button', () => {
    const wrapper = mountDialog()
    const cancelBtn = wrapper.findAll('button').find((b) => b.text() === 'Cancel')
    expect(cancelBtn).toBeDefined()
  })
})
