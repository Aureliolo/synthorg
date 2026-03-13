import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { ref, defineComponent, h } from 'vue'
import ApprovalActions from '@/components/approvals/ApprovalActions.vue'

vi.mock('@/composables/useAuth', () => ({
  useAuth: () => ({ canWrite: ref(true) }),
}))

const mockRequire = vi.fn((opts: { accept: () => void }) => {
  opts.accept()
})

vi.mock('primevue/useconfirm', () => ({
  useConfirm: () => ({ require: mockRequire }),
}))

// Stub PrimeVue Button as a simple button element
const ButtonStub = defineComponent({
  name: 'PvButton',
  props: ['label', 'icon', 'severity', 'size', 'outlined', 'text', 'disabled'],
  emits: ['click'],
  setup(props, { emit }) {
    return () =>
      h(
        'button',
        {
          disabled: props.disabled,
          onClick: () => emit('click'),
        },
        props.label,
      )
  },
})

// Stub PrimeVue Textarea as a native textarea element
const TextareaStub = defineComponent({
  name: 'PvTextarea',
  props: ['modelValue', 'rows', 'placeholder', 'class'],
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    return () =>
      h('textarea', {
        value: props.modelValue,
        rows: props.rows,
        placeholder: props.placeholder,
        onInput: (e: Event) => emit('update:modelValue', (e.target as HTMLTextAreaElement).value),
      })
  },
})

const globalStubs = {
  Button: ButtonStub,
  Textarea: TextareaStub,
}

describe('ApprovalActions', () => {
  beforeEach(() => {
    mockRequire.mockClear()
  })

  it('shows approve and reject buttons when status is pending and canWrite is true', () => {
    const wrapper = mount(ApprovalActions, {
      props: { approvalId: 'a1', status: 'pending' },
      global: { stubs: globalStubs },
    })
    expect(wrapper.text()).toContain('Approve')
    expect(wrapper.text()).toContain('Reject')
  })

  it('hides actions when status is approved', () => {
    const wrapper = mount(ApprovalActions, {
      props: { approvalId: 'a1', status: 'approved' },
      global: { stubs: globalStubs },
    })
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
  })

  it('hides actions when status is rejected', () => {
    const wrapper = mount(ApprovalActions, {
      props: { approvalId: 'a1', status: 'rejected' },
      global: { stubs: globalStubs },
    })
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
  })

  it('emits approve after confirm dialog accept', async () => {
    const wrapper = mount(ApprovalActions, {
      props: { approvalId: 'a1', status: 'pending' },
      global: { stubs: globalStubs },
    })
    const approveBtn = wrapper.findAll('button').find((b) => b.text().includes('Approve'))
    expect(approveBtn).toBeDefined()
    await approveBtn!.trigger('click')
    expect(mockRequire).toHaveBeenCalledOnce()
    expect(wrapper.emitted('approve')).toBeTruthy()
    expect(wrapper.emitted('approve')![0]).toEqual(['a1', ''])
  })

  it('shows reject form on reject button click', async () => {
    const wrapper = mount(ApprovalActions, {
      props: { approvalId: 'a1', status: 'pending' },
      global: { stubs: globalStubs },
    })
    const rejectBtn = wrapper.findAll('button').find((b) => b.text().includes('Reject'))
    await rejectBtn!.trigger('click')
    expect(wrapper.text()).toContain('Confirm Reject')
    expect(wrapper.text()).toContain('Back')
  })

  it('emits reject with reason after filling in reject form', async () => {
    const wrapper = mount(ApprovalActions, {
      props: { approvalId: 'a1', status: 'pending' },
      global: { stubs: globalStubs },
    })
    // Click initial Reject button to show reject form
    const rejectBtn = wrapper.findAll('button').find((b) => b.text().includes('Reject'))
    await rejectBtn!.trigger('click')

    // Find the reject reason textarea and set value
    const textareas = wrapper.findAll('textarea')
    const rejectTextarea = textareas[textareas.length - 1]
    await rejectTextarea.setValue('Not ready for production')

    // Click Confirm Reject
    const confirmBtn = wrapper.findAll('button').find((b) => b.text().includes('Confirm Reject'))
    await confirmBtn!.trigger('click')

    expect(wrapper.emitted('reject')).toBeTruthy()
    expect(wrapper.emitted('reject')![0]).toEqual(['a1', 'Not ready for production'])
  })

  it('does not emit reject when reason is empty', async () => {
    const wrapper = mount(ApprovalActions, {
      props: { approvalId: 'a1', status: 'pending' },
      global: { stubs: globalStubs },
    })
    // Show reject form
    const rejectBtn = wrapper.findAll('button').find((b) => b.text().includes('Reject'))
    await rejectBtn!.trigger('click')

    // Click Confirm Reject without filling in reason
    const confirmBtn = wrapper.findAll('button').find((b) => b.text().includes('Confirm Reject'))
    await confirmBtn!.trigger('click')

    expect(wrapper.emitted('reject')).toBeFalsy()
  })

  it('goes back from reject form when Back is clicked', async () => {
    const wrapper = mount(ApprovalActions, {
      props: { approvalId: 'a1', status: 'pending' },
      global: { stubs: globalStubs },
    })
    // Show reject form
    const rejectBtn = wrapper.findAll('button').find((b) => b.text().includes('Reject'))
    await rejectBtn!.trigger('click')
    expect(wrapper.text()).toContain('Confirm Reject')

    // Click Back
    const backBtn = wrapper.findAll('button').find((b) => b.text().includes('Back'))
    await backBtn!.trigger('click')

    // Should show the original approve/reject buttons again
    expect(wrapper.text()).toContain('Approve')
    expect(wrapper.text()).toContain('Reject')
    expect(wrapper.text()).not.toContain('Confirm Reject')
  })
})

describe('ApprovalActions (canWrite=false)', () => {
  it('hides actions when canWrite is false even if status is pending', async () => {
    // Reset module cache so doMock takes effect on fresh imports
    vi.resetModules()
    vi.doMock('@/composables/useAuth', () => ({
      useAuth: () => ({ canWrite: ref(false) }),
    }))
    vi.doMock('primevue/useconfirm', () => ({
      useConfirm: () => ({ require: mockRequire }),
    }))
    // Dynamic import to pick up the new mock
    const { default: ApprovalActionsNoWrite } = await import('@/components/approvals/ApprovalActions.vue')
    const wrapper = mount(ApprovalActionsNoWrite, {
      props: { approvalId: 'a1', status: 'pending' },
      global: { stubs: globalStubs },
    })
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
  })
})
