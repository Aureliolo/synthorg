/**
 * Users / RBAC store.
 *
 * Wires the ``/users`` listing + org-role grant/revoke endpoints into
 * a Zustand store with the canonical sentinel-error contract.
 */
/* eslint-disable security/detect-possible-timing-attacks --
   Comparisons against in-flight request tokens (plain monotonic
   ints) are not timing-sensitive secrets; they are how this store
   discards stale fetch responses. */
import { create } from 'zustand'

import {
  grantOrgRole as apiGrantOrgRole,
  listUsers as apiListUsers,
  revokeOrgRole as apiRevokeOrgRole,
  type GrantOrgRoleRequest,
  type UserResponse,
} from '@/api/endpoints/users'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'
import type { OrgRole } from '@/api/types/enums'

const log = createLogger('users')

// Page size for the cursor-paginated users list.  Centralised so
// the initial fetch + load-more agree on the same limit (the
// cursor-pagination contract requires it on every fetch-more call).
const USERS_PAGE_LIMIT = 50

interface UsersState {
  users: readonly UserResponse[]
  total: number | null
  nextCursor: string | null
  hasMore: boolean
  loading: boolean
  loadingMore: boolean
  error: string | null
  submitting: boolean

  fetchUsers: () => Promise<void>
  fetchMoreUsers: () => Promise<void>
  grantOrgRole: (
    userId: string,
    data: GrantOrgRoleRequest,
  ) => Promise<UserResponse | null>
  revokeOrgRole: (userId: string, role: OrgRole) => Promise<boolean>
}

export const useUsersStore = create<UsersState>()((set, get) => {
  // Monotonic request token used to discard stale fetch-more
  // results.  ``fetchUsers`` bumps the token; any in-flight
  // ``fetchMoreUsers`` whose token is no longer current discards
  // its result instead of appending stale page data.
  let listRequestToken = 0

  return {
  users: [],
  total: null,
  nextCursor: null,
  hasMore: false,
  loading: false,
  loadingMore: false,
  error: null,
  submitting: false,

  fetchUsers: async () => {
    // Bump the token so any in-flight fetchMoreUsers knows its
    // result is stale and should be discarded.
    const token = ++listRequestToken
    set({
      users: [],
      nextCursor: null,
      hasMore: false,
      loading: true,
      error: null,
    })
    try {
      const page = await apiListUsers({ limit: USERS_PAGE_LIMIT })
      if (token !== listRequestToken) return
      set({
        users: page.data,
        total: page.data.length,
        nextCursor: page.nextCursor,
        hasMore: page.hasMore,
        loading: false,
      })
    } catch (err) {
      log.error('Failed to fetch users', sanitizeForLog(err))
      if (token !== listRequestToken) return
      set({ loading: false, error: getErrorMessage(err) })
    }
  },

  fetchMoreUsers: async () => {
    const state = get()
    if (
      !state.hasMore ||
      !state.nextCursor ||
      state.loading ||
      state.loadingMore
    ) {
      return
    }
    const token = listRequestToken
    set({ loadingMore: true })
    try {
      const page = await apiListUsers({
        cursor: state.nextCursor,
        limit: USERS_PAGE_LIMIT,
      })
      // Drop the result if a newer ``fetchUsers`` (different token)
      // has superseded this load-more while we were awaiting; the
      // pre-await snapshot is no longer authoritative.  Clear
      // ``loadingMore`` before bailing so a stale request that loses
      // the token race does not leave the spinner pinned forever
      // (subsequent fetch-more calls would early-return on the
      // ``loadingMore`` guard).
      if (token !== listRequestToken) {
        set({ loadingMore: false })
        return
      }
      // Use the functional setter so concurrent mutations (e.g. a
      // grantOrgRole that lands while this page is in flight) are
      // not clobbered by the pre-await ``state.users`` snapshot.
      // Recompute ``total`` from the merged list so the count does
      // not go stale once additional pages land (the wire envelope
      // is cursor-only and no longer carries a server-side total).
      set((current) => {
        const merged = [...current.users, ...page.data]
        return {
          users: merged,
          total: merged.length,
          nextCursor: page.nextCursor,
          hasMore: page.hasMore,
          loadingMore: false,
        }
      })
    } catch (err) {
      log.error('Failed to fetch more users', sanitizeForLog(err))
      // Same reasoning as the success-path early return: clear
      // ``loadingMore`` even on the stale-token branch so the
      // pagination spinner cannot get stuck.
      if (token !== listRequestToken) {
        set({ loadingMore: false })
        return
      }
      set({ loadingMore: false, error: getErrorMessage(err) })
    }
  },

  grantOrgRole: async (userId, data) => {
    set({ submitting: true })
    try {
      const updated = await apiGrantOrgRole(userId, data)
      // Splice the updated user back into state without re-fetching.
      set((state) => ({
        users: state.users.map((u) => (u.id === userId ? updated : u)),
        submitting: false,
      }))
      useToastStore.getState().add({
        variant: 'success',
        title: `Granted ${data.role} to ${updated.username}`,
      })
      return updated
    } catch (err) {
      log.error('Failed to grant org role', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        title: 'Failed to grant role',
        description: getErrorMessage(err),
      })
      set({ submitting: false })
      return null
    }
  },

  revokeOrgRole: async (userId, role) => {
    set({ submitting: true })
    try {
      await apiRevokeOrgRole(userId, role)
      // The endpoint returns void; refetch the affected user via list
      // refresh to keep state coherent.  ``fetchUsers`` resets the
      // entire list which is fine for this rarely-used operation.
      void get().fetchUsers().catch((refetchErr: unknown) => {
        log.warn('users post-revoke refetch failed', sanitizeForLog(refetchErr))
      })
      useToastStore.getState().add({
        variant: 'success',
        title: `Revoked ${role}`,
      })
      set({ submitting: false })
      return true
    } catch (err) {
      log.error('Failed to revoke org role', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        title: 'Failed to revoke role',
        description: getErrorMessage(err),
      })
      set({ submitting: false })
      return false
    }
  },
  }
})
