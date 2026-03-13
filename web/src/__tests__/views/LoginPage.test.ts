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

vi.mock('primevue/usetoast', () => ({
  useToast: () => ({ add: vi.fn() }),
}))

vi.mock('@/router', () => ({
  router: {
    currentRoute: { value: { path: '/login' } },
    push: vi.fn(),
  },
}))

// Mock onUnmounted for useLoginLockout's interval cleanup
vi.mock('vue', async () => {
  const actual = await vi.importActual<typeof import('vue')>('vue')
  return { ...actual, onUnmounted: vi.fn() }
})

import LoginPage from '@/views/LoginPage.vue'
import { useAuthStore } from '@/stores/auth'

describe('LoginPage', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('renders sign-in heading and form fields', () => {
    const wrapper = mount(LoginPage)
    expect(wrapper.text()).toContain('SynthOrg')
    expect(wrapper.text()).toContain('Sign in to your dashboard')
    expect(wrapper.find('#username').exists()).toBe(true)
    expect(wrapper.find('#password').exists()).toBe(true)
  })

  it('renders labels for username and password', () => {
    const wrapper = mount(LoginPage)
    expect(wrapper.find('label[for="username"]').exists()).toBe(true)
    expect(wrapper.find('label[for="password"]').exists()).toBe(true)
  })

  it('disables submit button when fields are empty', () => {
    const wrapper = mount(LoginPage)
    const submitBtn = wrapper.find('button[type="submit"]')
    expect(submitBtn.attributes('disabled')).toBeDefined()
  })

  it('enables submit button when both fields have values', async () => {
    const wrapper = mount(LoginPage)
    await wrapper.find('#username').setValue('admin')
    await wrapper.find('#password').setValue('password123456')
    const submitBtn = wrapper.find('button[type="submit"]')
    expect(submitBtn.attributes('disabled')).toBeUndefined()
  })

  it('calls auth.login and navigates to / on success', async () => {
    const wrapper = mount(LoginPage)
    const auth = useAuthStore()
    auth.login = vi.fn().mockResolvedValue({ token: 'tok', expires_in: 3600 })
    auth.user = { id: 'u1', username: 'admin', role: 'ceo', must_change_password: false }

    await wrapper.find('#username').setValue('admin')
    await wrapper.find('#password').setValue('password123456')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(auth.login).toHaveBeenCalledWith('admin', 'password123456')
    expect(mockRouterPush).toHaveBeenCalledWith('/')
  })

  it('shows error message when login fails', async () => {
    const wrapper = mount(LoginPage)
    const auth = useAuthStore()
    auth.login = vi.fn().mockRejectedValue(new Error('Invalid credentials'))

    await wrapper.find('#username').setValue('admin')
    await wrapper.find('#password').setValue('wrongpassword1')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('Invalid credentials')
  })

  it('shows error element with role="alert"', async () => {
    const wrapper = mount(LoginPage)
    const auth = useAuthStore()
    auth.login = vi.fn().mockRejectedValue(new Error('Bad login'))

    await wrapper.find('#username').setValue('admin')
    await wrapper.find('#password').setValue('wrongpassword1')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    const alert = wrapper.find('[role="alert"]')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('Bad login')
  })

  it('has setup link', () => {
    const wrapper = mount(LoginPage)
    expect(wrapper.text()).toContain('First time? Set up admin account')
  })
})
