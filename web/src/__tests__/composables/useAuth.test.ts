import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/router', () => ({
  router: {
    currentRoute: { value: { path: '/' } },
    push: vi.fn(),
  },
}))

import { useAuth } from '@/composables/useAuth'
import { useAuthStore } from '@/stores/auth'

describe('useAuth', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('reports not authenticated when no user', () => {
    const { isAuthenticated, canWrite } = useAuth()
    expect(isAuthenticated.value).toBe(false)
    expect(canWrite.value).toBe(false)
  })

  it('reports authenticated when token exists', () => {
    localStorage.setItem('auth_token', 'test-token')
    localStorage.setItem('auth_token_expires_at', String(Date.now() + 3600_000))
    setActivePinia(createPinia())
    const { isAuthenticated } = useAuth()
    expect(isAuthenticated.value).toBe(true)
  })

  it('canWrite is true for write-capable roles', () => {
    const store = useAuthStore()
    store.user = { id: 'u1', username: 'mgr', role: 'manager', must_change_password: false }
    const { canWrite } = useAuth()
    expect(canWrite.value).toBe(true)
  })

  it('canWrite is false for read-only roles', () => {
    const store = useAuthStore()
    store.user = { id: 'u1', username: 'viewer', role: 'observer', must_change_password: false }
    const { canWrite } = useAuth()
    expect(canWrite.value).toBe(false)
  })

  it('exposes userRole from store', () => {
    const store = useAuthStore()
    store.user = { id: 'u1', username: 'ceo', role: 'ceo', must_change_password: false }
    const { userRole } = useAuth()
    expect(userRole.value).toBe('ceo')
  })

  it('exposes mustChangePassword from store', () => {
    const store = useAuthStore()
    store.user = { id: 'u1', username: 'new', role: 'ceo', must_change_password: true }
    const { mustChangePassword } = useAuth()
    expect(mustChangePassword.value).toBe(true)
  })
})
