import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAuthStore } from '@/stores/auth'

// Mock the router
vi.mock('@/router', () => ({
  router: {
    currentRoute: { value: { path: '/dashboard' } },
    push: vi.fn(),
  },
}))

// Mock the auth API module
const mockSetup = vi.fn()
const mockLogin = vi.fn()
const mockChangePassword = vi.fn()
const mockGetMe = vi.fn()

vi.mock('@/api/endpoints/auth', () => ({
  setup: (...args: unknown[]) => mockSetup(...args),
  login: (...args: unknown[]) => mockLogin(...args),
  changePassword: (...args: unknown[]) => mockChangePassword(...args),
  getMe: (...args: unknown[]) => mockGetMe(...args),
}))

describe('useAuthStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('initializes with no auth', () => {
    const store = useAuthStore()
    expect(store.isAuthenticated).toBe(false)
    expect(store.user).toBeNull()
    expect(store.token).toBeNull()
  })

  it('initializes with token from localStorage', () => {
    localStorage.setItem('auth_token', 'test-token')
    localStorage.setItem('auth_token_expires_at', String(Date.now() + 3600_000))
    const store = useAuthStore()
    expect(store.token).toBe('test-token')
    expect(store.isAuthenticated).toBe(true)
  })

  it('does not restore expired tokens', () => {
    localStorage.setItem('auth_token', 'test-token')
    localStorage.setItem('auth_token_expires_at', String(Date.now() - 1000))
    const store = useAuthStore()
    expect(store.token).toBeNull()
    expect(store.isAuthenticated).toBe(false)
  })

  it('logout clears auth state', () => {
    localStorage.setItem('auth_token', 'test-token')
    localStorage.setItem('auth_token_expires_at', String(Date.now() + 3600_000))
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

  describe('login', () => {
    it('sets token and fetches user on success', async () => {
      mockLogin.mockResolvedValue({
        token: 'new-token',
        expires_in: 3600,
        must_change_password: false,
      })
      mockGetMe.mockResolvedValue({
        id: 'user-1',
        username: 'admin',
        role: 'ceo',
        must_change_password: false,
      })

      const store = useAuthStore()
      const result = await store.login('admin', 'password123')

      expect(result.token).toBe('new-token')
      expect(store.token).toBe('new-token')
      expect(store.isAuthenticated).toBe(true)
      expect(store.user?.username).toBe('admin')
      expect(store.userRole).toBe('ceo')
      expect(localStorage.getItem('auth_token')).toBe('new-token')
    })

    it('clears auth if fetchUser fails after login', async () => {
      mockLogin.mockResolvedValue({
        token: 'new-token',
        expires_in: 3600,
        must_change_password: false,
      })
      mockGetMe.mockRejectedValue(new Error('Network error'))

      const store = useAuthStore()
      await expect(store.login('admin', 'password123')).rejects.toThrow(
        'Login succeeded but failed to load user profile. Please check your connection and try again.',
      )
      expect(store.token).toBeNull()
      expect(store.isAuthenticated).toBe(false)
    })

    it('sets loading during login', async () => {
      const store = useAuthStore()
      let loadingDuringCall = false
      mockLogin.mockImplementation(() => {
        loadingDuringCall = store.loading
        return Promise.resolve({
          token: 'new-token',
          expires_in: 3600,
          must_change_password: false,
        })
      })
      mockGetMe.mockResolvedValue({
        id: 'user-1',
        username: 'admin',
        role: 'ceo',
        must_change_password: false,
      })

      await store.login('admin', 'password123')
      expect(loadingDuringCall).toBe(true)
      expect(store.loading).toBe(false) // cleared in finally
    })
  })

  describe('setup', () => {
    it('sets token and fetches user on success', async () => {
      mockSetup.mockResolvedValue({
        token: 'setup-token',
        expires_in: 3600,
        must_change_password: true,
      })
      mockGetMe.mockResolvedValue({
        id: 'user-1',
        username: 'admin',
        role: 'ceo',
        must_change_password: true,
      })

      const store = useAuthStore()
      const result = await store.setup('admin', 'password123')

      expect(result.token).toBe('setup-token')
      expect(store.token).toBe('setup-token')
      expect(store.user?.id).toBe('user-1')
      expect(store.user?.role).toBe('ceo')
      expect(store.mustChangePassword).toBe(true)
      expect(mockGetMe).toHaveBeenCalled()
    })

    it('clears auth if fetchUser fails after setup', async () => {
      mockSetup.mockResolvedValue({
        token: 'setup-token',
        expires_in: 3600,
        must_change_password: true,
      })
      mockGetMe.mockRejectedValue(new Error('Network error'))

      const store = useAuthStore()
      await expect(store.setup('admin', 'password123')).rejects.toThrow(
        'Setup succeeded but failed to load user profile. Please check your connection and try again.',
      )
      expect(store.token).toBeNull()
      expect(store.isAuthenticated).toBe(false)
    })
  })

  describe('fetchUser', () => {
    it('clears auth on 401 response', async () => {
      localStorage.setItem('auth_token', 'test-token')
      localStorage.setItem('auth_token_expires_at', String(Date.now() + 3600_000))
      const axiosError = Object.assign(new Error('Unauthorized'), {
        isAxiosError: true,
        response: { status: 401 },
      })
      mockGetMe.mockRejectedValue(axiosError)

      const store = useAuthStore()
      await store.fetchUser()

      expect(store.token).toBeNull()
      expect(store.isAuthenticated).toBe(false)
    })

    it('does not clear auth on 500 — re-throws', async () => {
      localStorage.setItem('auth_token', 'test-token')
      localStorage.setItem('auth_token_expires_at', String(Date.now() + 3600_000))
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      try {
        const axiosError = Object.assign(new Error('Internal Server Error'), {
          isAxiosError: true,
          response: { status: 500 },
        })
        mockGetMe.mockRejectedValue(axiosError)

        const store = useAuthStore()
        await expect(store.fetchUser()).rejects.toThrow('Internal Server Error')

        expect(store.token).toBe('test-token')
        expect(store.isAuthenticated).toBe(true)
      } finally {
        consoleSpy.mockRestore()
      }
    })

    it('does nothing without token', async () => {
      const store = useAuthStore()
      await store.fetchUser()
      expect(mockGetMe).not.toHaveBeenCalled()
    })
  })

  describe('changePassword', () => {
    it('updates user with result', async () => {
      const updatedUser = {
        id: 'user-1',
        username: 'admin',
        role: 'ceo' as const,
        must_change_password: false,
      }
      mockChangePassword.mockResolvedValue(updatedUser)

      const store = useAuthStore()
      const result = await store.changePassword('old', 'new')

      expect(result).toEqual(updatedUser)
      expect(store.user).toEqual(updatedUser)
    })
  })
})
