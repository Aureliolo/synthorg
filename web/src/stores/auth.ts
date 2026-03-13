import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import * as authApi from '@/api/endpoints/auth'
import { isAxiosError } from '@/utils/errors'
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

  const isAuthenticated = computed(() => !!token.value)
  const mustChangePassword = computed(() => user.value?.must_change_password ?? false)
  const userRole = computed<HumanRole | null>(() => user.value?.role ?? null)

  function setToken(newToken: string, expiresIn: number) {
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

  async function setup(username: string, password: string) {
    loading.value = true
    try {
      const result = await authApi.setup({ username, password })
      setToken(result.token, result.expires_in)
      // Fetch full user info — mirrors login() pattern to avoid stale id/role
      try {
        await fetchUser()
      } catch {
        clearAuth()
        throw new Error('Setup succeeded but failed to load user profile. Please try again.')
      }
      return result
    } finally {
      loading.value = false
    }
  }

  async function login(username: string, password: string) {
    loading.value = true
    try {
      const result = await authApi.login({ username, password })
      setToken(result.token, result.expires_in)
      // Fetch full user info — if this fails, clear auth to avoid half-authenticated state
      try {
        await fetchUser()
      } catch {
        clearAuth()
        throw new Error('Login succeeded but failed to load user profile. Please try again.')
      }
      return result
    } finally {
      loading.value = false
    }
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
        console.error('Failed to fetch user profile:', err)
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
