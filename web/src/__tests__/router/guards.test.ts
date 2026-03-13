import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import type { NavigationGuardNext, RouteLocationNormalized } from 'vue-router'
import { authGuard } from '@/router/guards'
import { useAuthStore } from '@/stores/auth'

// Mock the router module (needed by auth store)
vi.mock('@/router', () => ({
  router: {
    currentRoute: { value: { path: '/' } },
    push: vi.fn(),
  },
}))

vi.mock('@/api/endpoints/auth', () => ({
  setup: vi.fn(),
  login: vi.fn(),
  changePassword: vi.fn(),
  getMe: vi.fn(),
}))

function createRoute(overrides: Partial<RouteLocationNormalized> = {}): RouteLocationNormalized {
  return {
    path: '/',
    name: undefined,
    params: {},
    query: {},
    hash: '',
    fullPath: '/',
    matched: [],
    meta: {},
    redirectedFrom: undefined,
    ...overrides,
  } as RouteLocationNormalized
}

describe('authGuard', () => {
  let next: NavigationGuardNext

  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    next = vi.fn()
  })

  it('redirects unauthenticated users to /login on protected routes', () => {
    const to = createRoute({ path: '/dashboard', fullPath: '/dashboard', meta: {} })
    const from = createRoute()

    authGuard(to, from, next)
    expect(next).toHaveBeenCalledWith({ path: '/login', query: { redirect: '/dashboard' } })
  })

  it('does not add redirect query for root path', () => {
    const to = createRoute({ path: '/', fullPath: '/', meta: {} })
    const from = createRoute()

    authGuard(to, from, next)
    expect(next).toHaveBeenCalledWith({ path: '/login', query: undefined })
  })

  it('allows authenticated users to access protected routes', () => {
    localStorage.setItem('auth_token', 'test-token')
    localStorage.setItem('auth_token_expires_at', String(Date.now() + 3600_000))
    // Re-create Pinia to pick up the token from localStorage
    setActivePinia(createPinia())
    // Force the store to read the token
    const store = useAuthStore()
    expect(store.isAuthenticated).toBe(true)

    const to = createRoute({ path: '/dashboard', meta: {} })
    const from = createRoute()

    authGuard(to, from, next)
    expect(next).toHaveBeenCalledWith()
  })

  it('allows unauthenticated users to access public routes', () => {
    const to = createRoute({ path: '/login', meta: { requiresAuth: false } })
    const from = createRoute()

    authGuard(to, from, next)
    expect(next).toHaveBeenCalledWith()
  })

  it('redirects authenticated users away from public routes to /', () => {
    localStorage.setItem('auth_token', 'test-token')
    localStorage.setItem('auth_token_expires_at', String(Date.now() + 3600_000))
    setActivePinia(createPinia())

    const to = createRoute({ path: '/login', meta: { requiresAuth: false } })
    const from = createRoute()

    authGuard(to, from, next)
    expect(next).toHaveBeenCalledWith('/')
  })

  it('redirects to settings when mustChangePassword is true', () => {
    localStorage.setItem('auth_token', 'test-token')
    localStorage.setItem('auth_token_expires_at', String(Date.now() + 3600_000))
    setActivePinia(createPinia())
    const store = useAuthStore()
    store.user = { id: 'u1', username: 'ceo', role: 'ceo', must_change_password: true }

    const to = createRoute({ path: '/tasks', name: 'tasks' as never, meta: {} })
    const from = createRoute()

    authGuard(to, from, next)
    expect(next).toHaveBeenCalledWith({ name: 'settings', query: { tab: 'user' } })
  })

  it('redirects settings without tab=user when mustChangePassword is true', () => {
    localStorage.setItem('auth_token', 'test-token')
    localStorage.setItem('auth_token_expires_at', String(Date.now() + 3600_000))
    setActivePinia(createPinia())
    const store = useAuthStore()
    store.user = { id: 'u1', username: 'ceo', role: 'ceo', must_change_password: true }

    const to = createRoute({ path: '/settings', name: 'settings' as never, meta: {} })
    const from = createRoute()

    authGuard(to, from, next)
    expect(next).toHaveBeenCalledWith({ name: 'settings', query: { tab: 'user' } })
  })

  it('allows settings?tab=user when mustChangePassword is true', () => {
    localStorage.setItem('auth_token', 'test-token')
    localStorage.setItem('auth_token_expires_at', String(Date.now() + 3600_000))
    setActivePinia(createPinia())
    const store = useAuthStore()
    store.user = { id: 'u1', username: 'ceo', role: 'ceo', must_change_password: true }

    const to = createRoute({ path: '/settings', name: 'settings' as never, query: { tab: 'user' }, meta: {} })
    const from = createRoute()

    authGuard(to, from, next)
    expect(next).toHaveBeenCalledWith()
  })
})
