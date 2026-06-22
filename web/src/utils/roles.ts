import type { HumanRole } from '@/api/types/enum-values.gen'

/**
 * Roles permitted to perform privileged mutations that the backend gates on a
 * senior role (promotion apply / cycle, SSRF-violation allow/deny). These are
 * UI-gating only; the server is the real authority. Kept as one constant so a
 * future role addition cannot be missed at one of the call sites.
 */
export const PRIVILEGED_MUTATION_ROLES = ['ceo', 'manager'] as const

/** True when the current role may perform privileged mutations. */
export function hasPrivilegedRole(userRole: HumanRole | null): boolean {
  return userRole !== null && (PRIVILEGED_MUTATION_ROLES as readonly HumanRole[]).includes(userRole)
}
