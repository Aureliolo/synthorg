import { renderHook } from '@testing-library/react'
import { useAuth } from '@/hooks/useAuth'
import { useAuthStore } from '@/stores/auth'
import type { UserInfoResponse } from '@/api/types/auth'
import type { HumanRole } from '@/api/types/enums'
import { WRITE_ROLES } from '@/utils/tasks'

function resetStore() {
  useAuthStore.setState({
    authStatus: 'unauthenticated',
    user: null,
    loading: false,
  })
}

describe('useAuth', () => {
  beforeEach(resetStore)

  it('returns not authenticated when unauthenticated', () => {
    const { result } = renderHook(() => useAuth())
    expect(result.current.isAuthenticated).toBe(false)
    expect(result.current.user).toBeNull()
    expect(result.current.userRole).toBeNull()
    expect(result.current.canWrite).toBe(false)
  })

  it('returns authenticated with user data', () => {
    const user: UserInfoResponse = {
      id: 'user-1',
      username: 'admin',
      role: 'ceo',
      must_change_password: false,
      org_roles: [],
      scoped_departments: [],
    }
    useAuthStore.setState({ authStatus: 'authenticated', user })

    const { result } = renderHook(() => useAuth())
    expect(result.current.isAuthenticated).toBe(true)
    expect(result.current.user).toEqual(user)
    expect(result.current.userRole).toBe('ceo')
    expect(result.current.mustChangePassword).toBe(false)
  })

  describe('canWrite', () => {
    const writeRoles = WRITE_ROLES
    const allRoles: readonly HumanRole[] = [
      'ceo',
      'manager',
      'board_member',
      'pair_programmer',
      'observer',
      'system',
    ]
    const readOnlyRoles = allRoles.filter(
      (r) => !(WRITE_ROLES as readonly string[]).includes(r),
    )

    for (const role of writeRoles) {
      it(`returns canWrite=true for ${role}`, () => {
        useAuthStore.setState({
          authStatus: 'authenticated',
          user: {
            id: '1',
            username: 'u',
            role,
            must_change_password: false,
            org_roles: [],
            scoped_departments: [],
          },
        })
        const { result } = renderHook(() => useAuth())
        expect(result.current.canWrite).toBe(true)
      })
    }

    for (const role of readOnlyRoles) {
      it(`returns canWrite=false for ${role}`, () => {
        useAuthStore.setState({
          authStatus: 'authenticated',
          user: {
            id: '1',
            username: 'u',
            role,
            must_change_password: false,
            org_roles: [],
            scoped_departments: [],
          },
        })
        const { result } = renderHook(() => useAuth())
        expect(result.current.canWrite).toBe(false)
      })
    }
  })

  it('returns mustChangePassword from user', () => {
    useAuthStore.setState({
      authStatus: 'authenticated',
      user: {
        id: '1',
        username: 'u',
        role: 'ceo',
        must_change_password: true,
        org_roles: [],
        scoped_departments: [],
      },
    })
    const { result } = renderHook(() => useAuth())
    expect(result.current.mustChangePassword).toBe(true)
  })

  it('returns false for mustChangePassword when user is null', () => {
    useAuthStore.setState({ authStatus: 'unknown', user: null })
    const { result } = renderHook(() => useAuth())
    expect(result.current.mustChangePassword).toBe(false)
  })
})
