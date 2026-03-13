import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import { ref, defineComponent, h, nextTick } from 'vue'
import type { Task } from '@/api/types'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  RouterLink: { template: '<a><slot /></a>' },
}))

const canWriteRef = ref(true)

vi.mock('@/composables/useAuth', () => ({
  useAuth: () => ({ canWrite: canWriteRef }),
}))

vi.mock('primevue/sidebar', () => ({
  default: defineComponent({
    props: ['visible', 'position'],
    emits: ['update:visible'],
    setup(props, { slots }) {
      return () => (props.visible ? h('div', { class: 'sidebar' }, [slots.header?.(), slots.default?.()]) : null)
    },
  }),
}))

vi.mock('primevue/button', () => ({
  default: defineComponent({
    props: ['label', 'icon', 'severity', 'text', 'size', 'outlined', 'disabled'],
    emits: ['click'],
    setup(props, { emit }) {
      return () =>
        h('button', { disabled: props.disabled, onClick: () => emit('click') }, props.label)
    },
  }),
}))

vi.mock('primevue/inputtext', () => ({
  default: defineComponent({
    props: ['modelValue'],
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          value: props.modelValue,
          onInput: (e: Event) => emit('update:modelValue', (e.target as HTMLInputElement).value),
        })
    },
  }),
}))

vi.mock('primevue/textarea', () => ({
  default: defineComponent({
    props: ['modelValue', 'placeholder', 'rows', 'ariaRequired'],
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('textarea', {
          value: props.modelValue,
          onInput: (e: Event) => emit('update:modelValue', (e.target as HTMLTextAreaElement).value),
        })
    },
  }),
}))

vi.mock('primevue/dropdown', () => ({
  default: defineComponent({
    props: ['modelValue', 'options', 'optionLabel', 'optionValue'],
    emits: ['update:modelValue'],
    setup(props) {
      return () => h('select', {}, String(props.modelValue))
    },
  }),
}))

vi.mock('@/components/common/StatusBadge.vue', () => ({
  default: {
    props: ['value', 'type'],
    template: '<span class="status-badge">{{ value }}</span>',
  },
}))

import TaskDetailPanel from '@/components/tasks/TaskDetailPanel.vue'

const mockTask: Task = {
  id: 't1',
  title: 'Fix bug',
  description: 'Fix the login bug',
  type: 'development',
  priority: 'high',
  status: 'in_progress',
  project: 'proj',
  created_by: 'admin',
  assigned_to: 'agent-1',
  reviewers: [],
  dependencies: [],
  artifacts_expected: [],
  acceptance_criteria: [],
  estimated_complexity: 'medium',
  budget_limit: 10,
  cost_usd: 2.5,
  deadline: null,
  max_retries: 3,
  parent_task_id: null,
  delegation_chain: [],
  task_structure: null,
  coordination_topology: 'auto',
  version: 1,
  created_at: '2026-03-12T10:00:00Z',
  updated_at: '2026-03-12T10:00:00Z',
}

describe('TaskDetailPanel', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    canWriteRef.value = true
  })

  function mountPanel(task: Task | null = mockTask, visible = true) {
    return mount(TaskDetailPanel, {
      props: { task, visible },
    })
  }

  it('renders task title and description', () => {
    const wrapper = mountPanel()
    expect(wrapper.text()).toContain('Fix bug')
    expect(wrapper.text()).toContain('Fix the login bug')
  })

  it('renders nothing when not visible', () => {
    const wrapper = mountPanel(mockTask, false)
    expect(wrapper.find('.sidebar').exists()).toBe(false)
  })

  it('renders nothing for null task', () => {
    const wrapper = mountPanel(null)
    expect(wrapper.text()).not.toContain('Fix bug')
  })

  it('shows metadata grid with status, priority, type, assignee', () => {
    const wrapper = mountPanel()
    expect(wrapper.text()).toContain('in_progress')
    expect(wrapper.text()).toContain('high')
    expect(wrapper.text()).toContain('development')
    expect(wrapper.text()).toContain('agent-1')
  })

  it('shows "Unassigned" when assigned_to is null', () => {
    const unassigned: Task = { ...mockTask, assigned_to: null }
    const wrapper = mountPanel(unassigned)
    expect(wrapper.text()).toContain('Unassigned')
  })

  it('shows Edit button when canWrite and task is not terminal', () => {
    const wrapper = mountPanel()
    const editBtn = wrapper.findAll('button').find((b) => b.text() === 'Edit')
    expect(editBtn).toBeDefined()
  })

  it('hides Edit button for terminal statuses', () => {
    const completed: Task = { ...mockTask, status: 'completed' }
    const wrapper = mountPanel(completed)
    const editBtn = wrapper.findAll('button').find((b) => b.text() === 'Edit')
    expect(editBtn).toBeUndefined()
  })

  it('hides Edit button when canWrite is false', () => {
    canWriteRef.value = false
    const wrapper = mountPanel()
    const editBtn = wrapper.findAll('button').find((b) => b.text() === 'Edit')
    expect(editBtn).toBeUndefined()
  })

  it('enters edit mode on Edit click and shows Save/Cancel', async () => {
    const wrapper = mountPanel()
    const editBtn = wrapper.findAll('button').find((b) => b.text() === 'Edit')
    await editBtn!.trigger('click')

    expect(wrapper.findAll('button').find((b) => b.text() === 'Save')).toBeDefined()
    expect(wrapper.findAll('button').find((b) => b.text() === 'Cancel')).toBeDefined()
  })

  it('emits save with updated fields on Save click', async () => {
    // Mount with null task, then set task to trigger watcher
    const wrapper = mount(TaskDetailPanel, {
      props: { task: null, visible: true },
    })
    await wrapper.setProps({ task: mockTask })
    await nextTick()

    const editBtn = wrapper.findAll('button').find((b) => b.text() === 'Edit')
    await editBtn!.trigger('click')

    const saveBtn = wrapper.findAll('button').find((b) => b.text() === 'Save')
    await saveBtn!.trigger('click')

    const emitted = wrapper.emitted('save')
    expect(emitted).toBeTruthy()
    expect(emitted![0][0]).toBe('t1')
    expect(emitted![0][1]).toMatchObject({
      title: 'Fix bug',
      description: 'Fix the login bug',
      priority: 'high',
    })
  })

  it('shows transition buttons for non-terminal status', () => {
    const wrapper = mountPanel()
    expect(wrapper.text()).toContain('Transition To')
    expect(wrapper.text()).toContain('in review')
  })

  it('hides transitions for completed tasks', () => {
    const completed: Task = { ...mockTask, status: 'completed' }
    const wrapper = mountPanel(completed)
    expect(wrapper.text()).not.toContain('Transition To')
  })

  it('emits transition event on transition button click', async () => {
    const wrapper = mountPanel()
    const transBtn = wrapper.findAll('button').find((b) => b.text() === 'in review')
    expect(transBtn).toBeDefined()
    await transBtn!.trigger('click')

    const emitted = wrapper.emitted('transition')
    expect(emitted).toBeTruthy()
    expect(emitted![0]).toEqual(['t1', 'in_review', 1])
  })

  it('shows Cancel Task button for non-terminal status', () => {
    const wrapper = mountPanel()
    const cancelBtn = wrapper.findAll('button').find((b) => b.text() === 'Cancel Task')
    expect(cancelBtn).toBeDefined()
  })

  it('shows cancel form with textarea on Cancel Task click', async () => {
    const wrapper = mountPanel()
    const cancelBtn = wrapper.findAll('button').find((b) => b.text() === 'Cancel Task')
    await cancelBtn!.trigger('click')
    await nextTick()

    expect(wrapper.text()).toContain('Confirm Cancel')
    expect(wrapper.find('textarea').exists()).toBe(true)
  })

  it('does not emit cancel when reason is empty', async () => {
    const wrapper = mountPanel()
    const cancelBtn = wrapper.findAll('button').find((b) => b.text() === 'Cancel Task')
    await cancelBtn!.trigger('click')

    const confirmBtn = wrapper.findAll('button').find((b) => b.text() === 'Confirm Cancel')
    await confirmBtn!.trigger('click')

    expect(wrapper.emitted('cancel')).toBeFalsy()
  })

  it('emits cancel with reason when form is submitted', async () => {
    const wrapper = mountPanel()
    const cancelBtn = wrapper.findAll('button').find((b) => b.text() === 'Cancel Task')
    await cancelBtn!.trigger('click')

    await wrapper.find('textarea').setValue('No longer needed')
    const confirmBtn = wrapper.findAll('button').find((b) => b.text() === 'Confirm Cancel')
    await confirmBtn!.trigger('click')

    const emitted = wrapper.emitted('cancel')
    expect(emitted).toBeTruthy()
    expect(emitted![0]).toEqual(['t1', 'No longer needed'])
  })
})
