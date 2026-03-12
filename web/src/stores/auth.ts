import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import * as authApi from '@/api/endpoints/auth'
import type { HumanRole, UserInfoResponse } from '@/api/types'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(localStorage.getItem('auth_token'))
  const user = ref<UserInfoResponse | null>(null)
  const loading = ref(false)

  const isAuthenticated = computed(() => !!token.value)
  const mustChangePassword = computed(() => user.value?.must_change_password ?? false)
  const userRole = computed<HumanRole | null>(() => user.value?.role ?? null)

  function setToken(newToken: string, expiresIn: number) {
    token.value = newToken
    localStorage.setItem('auth_token', newToken)
    // Schedule token cleanup
    setTimeout(() => {
      token.value = null
      localStorage.removeItem('auth_token')
    }, expiresIn * 1000)
  }

  function clearAuth() {
    token.value = null
    user.value = null
    localStorage.removeItem('auth_token')
  }

  async function setup(username: string, password: string) {
    loading.value = true
    try {
      const result = await authApi.setup({ username, password })
      setToken(result.token, result.expires_in)
      user.value = {
        id: '',
        username,
        role: 'ceo',
        must_change_password: result.must_change_password,
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
      // Fetch full user info
      await fetchUser()
      return result
    } finally {
      loading.value = false
    }
  }

  async function fetchUser() {
    if (!token.value) return
    try {
      user.value = await authApi.getMe()
    } catch {
      clearAuth()
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
