import { computed } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { WRITE_ROLES } from '@/utils/constants'

/** Auth state helpers for components. */
export function useAuth() {
  const store = useAuthStore()

  const isAuthenticated = computed(() => store.isAuthenticated)
  const user = computed(() => store.user)
  const userRole = computed(() => store.userRole)
  const mustChangePassword = computed(() => store.mustChangePassword)

  const canWrite = computed(() => {
    const role = userRole.value
    return role !== null && (WRITE_ROLES as ReadonlyArray<string>).includes(role)
  })

  return {
    isAuthenticated,
    user,
    userRole,
    mustChangePassword,
    canWrite,
  }
}
