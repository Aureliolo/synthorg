import type { HumanRole } from '@/api/types/enum-values.gen'

/**
 * Roles permitted to perform privileged mutations that the backend gates on a
 * senior role (promotion apply / cycle, SSRF-violation allow/deny). These are
 * UI-gating only; the server is the real authority. Kept as one constant so a
 * future role addition cannot be missed at one of the call sites.
 */
const PRIVILEGED_MUTATION_ROLES: readonly HumanRole[] = ['ceo', 'manager']

/** True when the current role may perform privileged mutations. */
export function hasPrivilegedRole(userRole: HumanRole | null): boolean {
  return userRole !== null && PRIVILEGED_MUTATION_ROLES.includes(userRole)
}
