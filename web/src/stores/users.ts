/**
 * Users / RBAC store.
 *
 * Wires the ``/users`` listing + org-role grant/revoke endpoints into
 * a Zustand store with the canonical sentinel-error contract.
 */
import type { StoreApi } from 'zustand'
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

type UsersSet = StoreApi<UsersState>['setState']
type UsersGet = StoreApi<UsersState>['getState']

let listRequestToken = 0

async function fetchUsersImpl(set: UsersSet): Promise<void> {
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
}

async function fetchMoreUsersImpl(
  set: UsersSet,
  get: UsersGet,
): Promise<void> {
  const state = get()
  if (
    !state.hasMore
    || !state.nextCursor
    || state.loading
    || state.loadingMore
  ) return
  const token = listRequestToken
  set({ loadingMore: true })
  try {
    const page = await apiListUsers({
      cursor: state.nextCursor,
      limit: USERS_PAGE_LIMIT,
    })
    if (token !== listRequestToken) {
      set({ loadingMore: false })
      return
    }
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
    if (token !== listRequestToken) {
      set({ loadingMore: false })
      return
    }
    set({ loadingMore: false, error: getErrorMessage(err) })
  }
}

async function grantOrgRoleImpl(
  set: UsersSet,
  userId: string,
  data: GrantOrgRoleRequest,
): Promise<UserResponse | null> {
  set({ submitting: true })
  try {
    const updated = await apiGrantOrgRole(userId, data)
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
}

async function revokeOrgRoleImpl(
  set: UsersSet,
  get: UsersGet,
  userId: string,
  role: OrgRole,
): Promise<boolean> {
  set({ submitting: true })
  try {
    await apiRevokeOrgRole(userId, role)
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

  fetchUsers: () => fetchUsersImpl(set),
  fetchMoreUsers: () => fetchMoreUsersImpl(set, get),
  grantOrgRole: (userId, data) => grantOrgRoleImpl(set, userId, data),
  revokeOrgRole: (userId, role) => revokeOrgRoleImpl(set, get, userId, role),
}))
