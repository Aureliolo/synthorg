import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import * as authApi from '@/api/endpoints/auth'
import { getErrorMessage, isAxiosError } from '@/utils/errors'
import { router } from '@/router'
import type { HumanRole, UserInfoResponse } from '@/api/types'

export const useAuthStore = defineStore('auth', () => {
  // Restore token only if not expired
  const storedToken = localStorage.getItem('auth_token')
  const expiresAt = Number(localStorage.getItem('auth_token_expires_at') ?? 0)
  const initialToken = storedToken && Date.now() < expiresAt ? storedToken : null
  if (!initialToken) {
    localStorage.removeItem('auth_token')
    localStorage.removeItem('auth_token_expires_at')
  }

  const token = ref<string | null>(initialToken)
  const user = ref<UserInfoResponse | null>(null)
  const loading = ref(false)

  let expiryTimer: ReturnType<typeof setTimeout> | null = null

  // Schedule expiry cleanup for restored token
  if (initialToken && expiresAt > Date.now()) {
    expiryTimer = setTimeout(() => {
      clearAuth()
    }, expiresAt - Date.now())
  }

  // Clean up timer during HMR to avoid stale timers on dev reloads
  if (import.meta.hot) {
    import.meta.hot.dispose(() => {
      if (expiryTimer) {
        clearTimeout(expiryTimer)
        expiryTimer = null
      }
    })
  }

  const isAuthenticated = computed(() => !!token.value)
  const mustChangePassword = computed(() => user.value?.must_change_password ?? false)
  const userRole = computed<HumanRole | null>(() => user.value?.role ?? null)

  function setToken(newToken: string, expiresIn: number) {
    if (expiresIn <= 0) {
      console.error('setToken: invalid expiresIn', expiresIn)
      return
    }
    // Clear any existing expiry timer to prevent stale timer from killing new session
    if (expiryTimer) {
      clearTimeout(expiryTimer)
      expiryTimer = null
    }

    token.value = newToken
    const expiresAtMs = Date.now() + expiresIn * 1000
    localStorage.setItem('auth_token', newToken)
    localStorage.setItem('auth_token_expires_at', String(expiresAtMs))

    // Schedule token cleanup
    expiryTimer = setTimeout(() => {
      clearAuth()
    }, expiresIn * 1000)
  }

  function clearAuth() {
    if (expiryTimer) {
      clearTimeout(expiryTimer)
      expiryTimer = null
    }
    token.value = null
    user.value = null
    localStorage.removeItem('auth_token')
    localStorage.removeItem('auth_token_expires_at')
    // Redirect to login if not already there
    if (router.currentRoute.value.path !== '/login' && router.currentRoute.value.path !== '/setup') {
      router.push('/login')
    }
  }

  /** Common post-auth flow: set token, fetch user profile, handle failures. */
  async function performAuthFlow(
    authFn: () => Promise<{ token: string; expires_in: number }>,
    flowName: string,
  ) {
    loading.value = true
    try {
      const result = await authFn()
      setToken(result.token, result.expires_in)
      try {
        await fetchUser()
      } catch (fetchErr) {
        // fetchUser already clears auth on 401 (invalid token) and doesn't throw.
        // If we get here, it's a transient error (network/5xx) — the token may be
        // valid but we can't load the profile. Clear auth since the app can't
        // function without user data, but use a distinct error message.
        if (isAxiosError(fetchErr) && fetchErr.response?.status === 401) {
          // 401 during fetchUser means the just-issued token is already invalid
          clearAuth()
          throw new Error(`${flowName} failed: invalid session. Please try again.`)
        }
        clearAuth()
        throw new Error(`${flowName} succeeded but failed to load user profile. Please check your connection and try again.`)
      }
      // If fetchUser silently cleared auth (e.g. 401), the flow should not succeed
      if (!user.value) {
        clearAuth()
        throw new Error(`${flowName} succeeded but failed to load user profile. Please try again.`)
      }
      return result
    } finally {
      loading.value = false
    }
  }

  async function setup(username: string, password: string) {
    return performAuthFlow(() => authApi.setup({ username, password }), 'Setup')
  }

  async function login(username: string, password: string) {
    return performAuthFlow(() => authApi.login({ username, password }), 'Login')
  }

  async function fetchUser() {
    if (!token.value) return
    try {
      user.value = await authApi.getMe()
    } catch (err) {
      // Only clear auth on 401 (invalid/expired token)
      // Transient errors (network, 500) should NOT log the user out
      if (isAxiosError(err) && err.response?.status === 401) {
        clearAuth()
      } else {
        console.error('Failed to fetch user profile:', getErrorMessage(err))
        throw err
      }
    }
  }

  async function changePassword(currentPassword: string, newPassword: string) {
    loading.value = true
    try {
      const result = await authApi.changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      })
      user.value = result
      return result
    } catch (err) {
      throw new Error(getErrorMessage(err))
    } finally {
      loading.value = false
    }
  }

  function logout() {
    clearAuth()
  }

  return {
    token,
    user,
    loading,
    isAuthenticated,
    mustChangePassword,
    userRole,
    setup,
    login,
    fetchUser,
    changePassword,
    logout,
  }
})
