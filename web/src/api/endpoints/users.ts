import {
  ApiRequestError,
  apiClient,
  unwrap,
  unwrapPaginated,
  unwrapVoid,
  type PaginatedResult,
} from '../client'
import type { OrgRole } from '../types/enums'
import type { ApiResponse, PaginatedResponse, PaginationParams } from '../types/http'
import type { UserResponse as UserResponseWire } from '@/api/types/auth'

// Derive from the generated wire type so id/username/role/timestamps stay in
// lockstep with the backend, but keep ``org_roles`` narrowed to ``OrgRole``:
// the backend only ever emits ``OrgRole`` values here (the OpenAPI schema
// widens the StrEnum list to ``string[]``), and the users UI depends on the
// narrowing exactly as it relies on the already-narrowed ``role`` field.
export type UserResponse = Omit<UserResponseWire, 'org_roles'> & {
  readonly org_roles: readonly OrgRole[]
}

// Kept hand-written: the discriminated union enforces that
// ``scoped_departments`` is required only for ``department_admin`` and absent
// for every other role, a constraint the flat generated struct cannot express.
export type GrantOrgRoleRequest =
  | { role: 'department_admin'; scoped_departments: readonly string[] }
  | { role: Exclude<OrgRole, 'department_admin'>; scoped_departments?: never }

export async function listUsers(
  params?: PaginationParams,
): Promise<PaginatedResult<UserResponse>> {
  const response = await apiClient.get<PaginatedResponse<UserResponse>>('/users', { params })
  return unwrapPaginated<UserResponse>(response)
}

export async function grantOrgRole(userId: string, data: GrantOrgRoleRequest): Promise<UserResponse> {
  // The backend rejects a department_admin grant with an empty scope (422);
  // TypeScript cannot express "non-empty array", so guard before dispatch.
  if (data.role === 'department_admin' && data.scoped_departments.length === 0) {
    throw new ApiRequestError(
      'A department admin grant requires at least one scoped department',
    )
  }
  const response = await apiClient.post<ApiResponse<UserResponse>>(
    `/users/${encodeURIComponent(userId)}/org-roles`,
    data,
  )
  return unwrap(response)
}

export async function revokeOrgRole(userId: string, role: OrgRole): Promise<void> {
  const response = await apiClient.delete<ApiResponse<null>>(
    `/users/${encodeURIComponent(userId)}/org-roles/${encodeURIComponent(role)}`,
  )
  unwrapVoid(response)
}
