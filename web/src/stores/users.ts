/**
 * Users / RBAC store.
 *
 * Wires the ``/users`` listing + org-role grant/revoke endpoints into
 * a Zustand store with the canonical sentinel-error contract.
 */
import { create } from 'zustand'

import {
  grantOrgRole as apiGrantOrgRole,
  listUsers as apiListUsers,
  revokeOrgRole as apiRevokeOrgRole,
  type GrantOrgRoleRequest,
  type UserResponse,
} from '@/api/endpoints/users'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'
import type { OrgRole } from '@/api/types/enums'

const log = createLogger('users')

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

export const useUsersStore = create<UsersState>()((set, get) => ({
  users: [],
  total: null,
  nextCursor: null,
  hasMore: false,
  loading: false,
  loadingMore: false,
  error: null,
  submitting: false,

  fetchUsers: async () => {
    set({
      users: [],
      nextCursor: null,
      hasMore: false,
      loading: true,
      error: null,
    })
    try {
      const page = await apiListUsers()
      set({
        users: page.data,
        total: page.total,
        nextCursor: page.nextCursor,
        hasMore: page.hasMore,
        loading: false,
      })
    } catch (err) {
      log.warn('Failed to fetch users:', getErrorMessage(err))
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
    set({ loadingMore: true })
    try {
      const page = await apiListUsers({ cursor: state.nextCursor })
      set({
        users: [...state.users, ...page.data],
        nextCursor: page.nextCursor,
        hasMore: page.hasMore,
        loadingMore: false,
      })
    } catch (err) {
      log.warn('Failed to fetch more users:', getErrorMessage(err))
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
      log.warn('Failed to grant org role:', getErrorMessage(err))
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
      void get().fetchUsers()
      useToastStore.getState().add({
        variant: 'success',
        title: `Revoked ${role}`,
      })
      set({ submitting: false })
      return true
    } catch (err) {
      log.warn('Failed to revoke org role:', getErrorMessage(err))
      useToastStore.getState().add({
        variant: 'error',
        title: 'Failed to revoke role',
        description: getErrorMessage(err),
      })
      set({ submitting: false })
      return false
    }
  },
}))
