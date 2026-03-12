import { computed } from 'vue'
import { useAuthStore } from '@/stores/auth'

/** Auth state helpers for components. */
export function useAuth() {
  const store = useAuthStore()

  const isAuthenticated = computed(() => store.isAuthenticated)
  const user = computed(() => store.user)
  const userRole = computed(() => store.userRole)
  const mustChangePassword = computed(() => store.mustChangePassword)

  const canWrite = computed(() => {
    const role = store.userRole
    return role === 'ceo' || role === 'manager' || role === 'board_member' || role === 'pair_programmer'
  })

  return {
    isAuthenticated,
    user,
    userRole,
    mustChangePassword,
    canWrite,
  }
}
