import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import { defineComponent, h } from 'vue'

const mockRouterPush = vi.fn()

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: mockRouterPush }),
  RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
}))

vi.mock('primevue/inputtext', () => ({
  default: defineComponent({
    props: ['modelValue', 'id', 'type', 'placeholder', 'autocomplete', 'ariaDescribedby'],
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      return () =>
        h('input', {
          id: props.id,
          type: props.type ?? 'text',
          value: props.modelValue,
          onInput: (e: Event) => emit('update:modelValue', (e.target as HTMLInputElement).value),
        })
    },
  }),
}))

vi.mock('primevue/button', () => ({
  default: defineComponent({
    props: ['label', 'icon', 'type', 'loading', 'disabled'],
    emits: ['click'],
    setup(props, { emit }) {
      return () =>
        h(
          'button',
          {
            type: props.type ?? 'button',
            disabled: props.disabled || props.loading,
            onClick: () => emit('click'),
          },
          props.label,
        )
    },
  }),
}))

vi.mock('@/router', () => ({
  router: {
    currentRoute: { value: { path: '/setup' } },
    push: vi.fn(),
  },
}))

// Mock onUnmounted for useLoginLockout's interval cleanup
vi.mock('vue', async () => {
  const actual = await vi.importActual<typeof import('vue')>('vue')
  return { ...actual, onUnmounted: vi.fn() }
})

import SetupPage from '@/views/SetupPage.vue'
import { useAuthStore } from '@/stores/auth'

describe('SetupPage', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('renders setup heading and form fields', () => {
    const wrapper = mount(SetupPage)
    expect(wrapper.text()).toContain('Initial Setup')
    expect(wrapper.text()).toContain('Create the first admin (CEO) account')
    expect(wrapper.find('#username').exists()).toBe(true)
    expect(wrapper.find('#password').exists()).toBe(true)
    expect(wrapper.find('#confirm').exists()).toBe(true)
  })

  it('renders labels for all form fields', () => {
    const wrapper = mount(SetupPage)
    expect(wrapper.find('label[for="username"]').exists()).toBe(true)
    expect(wrapper.find('label[for="password"]').exists()).toBe(true)
    expect(wrapper.find('label[for="confirm"]').exists()).toBe(true)
  })

  it('disables submit button when fields are empty', () => {
    const wrapper = mount(SetupPage)
    const submitBtn = wrapper.find('button[type="submit"]')
    expect(submitBtn.attributes('disabled')).toBeDefined()
  })

  it('shows password mismatch error', async () => {
    const wrapper = mount(SetupPage)
    await wrapper.find('#username').setValue('admin')
    await wrapper.find('#password').setValue('password123456')
    await wrapper.find('#confirm').setValue('differentpass12')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('Passwords do not match')
  })

  it('shows minimum password length error', async () => {
    const wrapper = mount(SetupPage)
    await wrapper.find('#username').setValue('admin')
    await wrapper.find('#password').setValue('short')
    await wrapper.find('#confirm').setValue('short')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('Password must be at least')
  })

  it('calls auth.setup and navigates to / on success', async () => {
    const wrapper = mount(SetupPage)
    const auth = useAuthStore()
    auth.setup = vi.fn().mockResolvedValue({ token: 'tok', expires_in: 3600 })

    await wrapper.find('#username').setValue('admin')
    await wrapper.find('#password').setValue('securepassword1')
    await wrapper.find('#confirm').setValue('securepassword1')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(auth.setup).toHaveBeenCalledWith('admin', 'securepassword1')
    expect(mockRouterPush).toHaveBeenCalledWith('/')
  })

  it('shows error message on setup failure', async () => {
    const wrapper = mount(SetupPage)
    const auth = useAuthStore()
    auth.setup = vi.fn().mockRejectedValue(new Error('Admin already exists'))

    await wrapper.find('#username').setValue('admin')
    await wrapper.find('#password').setValue('securepassword1')
    await wrapper.find('#confirm').setValue('securepassword1')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('Admin already exists')
  })

  it('has sign-in link pointing to /login', () => {
    const wrapper = mount(SetupPage)
    const link = wrapper.find('a[href="/login"]')
    expect(link.exists()).toBe(true)
    expect(link.text()).toContain('Already have an account? Sign in')
  })
})
