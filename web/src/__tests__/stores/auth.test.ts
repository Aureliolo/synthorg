import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAuthStore } from '@/stores/auth'

// Mock the auth API module
vi.mock('@/api/endpoints/auth', () => ({
  setup: vi.fn(),
  login: vi.fn(),
  changePassword: vi.fn(),
  getMe: vi.fn(),
}))

describe('useAuthStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('initializes with no auth', () => {
    const store = useAuthStore()
    expect(store.isAuthenticated).toBe(false)
    expect(store.user).toBeNull()
    expect(store.token).toBeNull()
  })

  it('initializes with token from localStorage', () => {
    localStorage.setItem('auth_token', 'test-token')
    const store = useAuthStore()
    expect(store.token).toBe('test-token')
    expect(store.isAuthenticated).toBe(true)
  })

  it('logout clears auth state', () => {
    localStorage.setItem('auth_token', 'test-token')
    const store = useAuthStore()
    store.logout()
    expect(store.token).toBeNull()
    expect(store.user).toBeNull()
    expect(store.isAuthenticated).toBe(false)
    expect(localStorage.getItem('auth_token')).toBeNull()
  })

  it('mustChangePassword defaults to false', () => {
    const store = useAuthStore()
    expect(store.mustChangePassword).toBe(false)
  })

  it('userRole is null when no user', () => {
    const store = useAuthStore()
    expect(store.userRole).toBeNull()
  })
})
