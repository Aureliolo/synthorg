/**
 * Users / RBAC management page.
 *
 * Lists human users and lets the operator grant or revoke org roles
 * (owner / department_admin / editor / viewer).  Department-scoped
 * ``department_admin`` grants accept a tag-style list of departments.
 *
 * Backed by ``/users`` + ``/users/{id}/org-roles`` endpoints; the
 * invite / delete-user surface is not yet exposed in the TS endpoint
 * module and is therefore out of scope here.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Plus, ShieldCheck, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { SectionCard } from '@/components/ui/section-card'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { SearchInput } from '@/components/ui/search-input'
import { Skeleton } from '@/components/ui/skeleton'
import { useUsersStore } from '@/stores/users'
import { formatDateTime } from '@/utils/format'
import { getLocale } from '@/utils/locale'
import type { OrgRole } from '@/api/types/enums'
import type { UserResponse } from '@/api/endpoints/users'
import { GrantRoleDialog } from './users/GrantRoleDialog'
import { cn } from '@/lib/utils'
import { ROLE_BADGE_COLORS } from '@/styles/status-colors'

const LOCALE = getLocale()

interface RevokeTarget {
  user: UserResponse
  role: OrgRole
}

function RolePill({
  role,
  scopedDepartments,
  onRevoke,
  busy,
}: {
  role: OrgRole
  scopedDepartments?: readonly string[] | undefined
  onRevoke: () => void
  busy: boolean
}) {
  const scopeSummary =
    role === 'department_admin' && scopedDepartments && scopedDepartments.length > 0
      ? ` (${scopedDepartments.length})`
      : ''
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium',
        ROLE_BADGE_COLORS[role],
      )}
    >
      {role}
      {scopeSummary}
      <button
        type="button"
        onClick={onRevoke}
        disabled={busy}
        aria-label={`Revoke ${role}`}
        className="rounded transition-colors hover:bg-surface/50 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <X aria-hidden="true" className="size-3" />
      </button>
    </span>
  )
}

function UserCard({
  user,
  submitting,
  onGrant,
  onRevoke,
}: {
  user: UserResponse
  submitting: boolean
  onGrant: (user: UserResponse) => void
  onRevoke: (target: RevokeTarget) => void
}) {
  return (
    <SectionCard
      title={user.username}
      icon={ShieldCheck}
      action={
        <Button variant="secondary" onClick={() => onGrant(user)}>
          <Plus aria-hidden="true" className="size-4" />
          Grant role
        </Button>
      }
    >
      <dl className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-text-secondary">Role</dt>
          <dd className="text-foreground">{user.role}</dd>
        </div>
        <div>
          <dt className="text-text-secondary">Created</dt>
          <dd className="text-foreground">{formatDateTime(user.created_at)}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-text-secondary">Org roles</dt>
          <dd className="mt-1 flex flex-wrap gap-1">
            {user.org_roles.length === 0 && (
              <span className="text-text-secondary">None granted</span>
            )}
            {user.org_roles.map((role) => (
              <RolePill
                key={role}
                role={role}
                scopedDepartments={role === 'department_admin' ? user.scoped_departments : undefined}
                onRevoke={() => onRevoke({ user, role })}
                busy={submitting}
              />
            ))}
          </dd>
        </div>
      </dl>
    </SectionCard>
  )
}

interface UsersContentProps {
  loading: boolean
  usersCount: number
  sortedUsers: readonly UserResponse[]
  trimmedQuery: string
  error: string | null
  submitting: boolean
  onGrant: (user: UserResponse) => void
  onRevoke: (target: RevokeTarget) => void
}

function UsersContent({
  loading,
  usersCount,
  sortedUsers,
  trimmedQuery,
  error,
  submitting,
  onGrant,
  onRevoke,
}: UsersContentProps) {
  if (loading && usersCount === 0) {
    return (
      <div className="flex flex-col gap-grid-gap">
        {[1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-20 w-full" />
        ))}
      </div>
    )
  }
  // Use ``sortedUsers`` (the post-search-filter view) so an active query
  // that yields nothing renders a search-empty message instead of a blank
  // list. Gated on ``!error`` so a failed fetch doesn't render alongside
  // the error banner as a misleading "No users" message.
  if (!error && sortedUsers.length === 0) {
    return (
      <EmptyState
        title={trimmedQuery ? 'No matching users' : 'No users'}
        description={
          trimmedQuery
            ? 'Try a different search term or clear the field above.'
            : "Human users with dashboard access will appear here once they're provisioned."
        }
      />
    )
  }
  if (sortedUsers.length === 0) return null
  return (
    <ul className="grid grid-cols-1 gap-grid-gap md:grid-cols-2 lg:grid-cols-3">
      {sortedUsers.map((user) => (
        <li key={user.id}>
          <UserCard user={user} submitting={submitting} onGrant={onGrant} onRevoke={onRevoke} />
        </li>
      ))}
    </ul>
  )
}

function RevokeRoleDialog({
  revokingTarget,
  submitting,
  onCancel,
  onConfirm,
}: {
  revokingTarget: RevokeTarget | null
  submitting: boolean
  onCancel: () => void
  onConfirm: (target: RevokeTarget) => Promise<void>
}) {
  return (
    <ConfirmDialog
      open={revokingTarget !== null}
      onOpenChange={(next) => {
        if (!next) onCancel()
      }}
      title={
        revokingTarget
          ? `Revoke ${revokingTarget.role} from ${revokingTarget.user.username}?`
          : 'Revoke role'
      }
      description="The user will lose this org role immediately."
      variant="destructive"
      confirmLabel={submitting ? 'Revoking…' : 'Revoke'}
      loading={submitting}
      onConfirm={async () => {
        if (revokingTarget) await onConfirm(revokingTarget)
      }}
    />
  )
}

function useUsersPageController() {
  const users = useUsersStore((s) => s.users)
  const loading = useUsersStore((s) => s.loading)
  const loadingMore = useUsersStore((s) => s.loadingMore)
  const error = useUsersStore((s) => s.error)
  const hasMore = useUsersStore((s) => s.hasMore)
  const submitting = useUsersStore((s) => s.submitting)
  const fetchUsers = useUsersStore((s) => s.fetchUsers)
  const fetchMoreUsers = useUsersStore((s) => s.fetchMoreUsers)
  const revokeOrgRole = useUsersStore((s) => s.revokeOrgRole)

  const [grantingFor, setGrantingFor] = useState<UserResponse | null>(null)
  const [revokingTarget, setRevokingTarget] = useState<RevokeTarget | null>(null)
  const [searchQuery, setSearchQuery] = useState('')

  useEffect(() => {
    void fetchUsers()
  }, [fetchUsers])

  // Trim once so both filtering and the empty-state copy agree on what
  // counts as "active search" (a whitespace-only query must not read as
  // a filter).
  const trimmedQuery = searchQuery.trim()
  const sortedUsers = useMemo(() => {
    const q = trimmedQuery.toLowerCase()
    const filtered = q
      ? users.filter(
          (u) =>
            u.username.toLowerCase().includes(q) || u.role.toLowerCase().includes(q),
        )
      : users
    return [...filtered].sort((a, b) => a.username.localeCompare(b.username, LOCALE))
  }, [users, trimmedQuery])

  const handleRevoke = useCallback(
    async (target: RevokeTarget): Promise<void> => {
      const ok = await revokeOrgRole(target.user.id, target.role)
      // Only dismiss on success; on failure the store has already surfaced
      // the error toast and we keep the dialog open so the user can retry.
      if (ok) setRevokingTarget(null)
    },
    [revokeOrgRole, setRevokingTarget],
  )

  return {
    users, loading, loadingMore, error, hasMore, submitting, fetchUsers, fetchMoreUsers,
    grantingFor, setGrantingFor, revokingTarget, setRevokingTarget, searchQuery, setSearchQuery,
    trimmedQuery, sortedUsers, handleRevoke,
  }
}

export default function UsersPage() {
  const c = useUsersPageController()

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Users"
        description="Human operators with dashboard access and their org-role grants."
        count={c.sortedUsers.length}
      />

      {c.error && (
        <ErrorBanner
          severity="error"
          title="Could not load users"
          description={c.error}
          onRetry={() => {
            void c.fetchUsers()
          }}
        />
      )}

      <SearchFilterSort
        search={
          <SearchInput
            value={c.searchQuery}
            onChange={c.setSearchQuery}
            placeholder="Search users by name or role"
            ariaLabel="Search users"
          />
        }
      />

      <UsersContent
        loading={c.loading}
        usersCount={c.users.length}
        sortedUsers={c.sortedUsers}
        trimmedQuery={c.trimmedQuery}
        error={c.error}
        submitting={c.submitting}
        onGrant={c.setGrantingFor}
        onRevoke={c.setRevokingTarget}
      />

      {c.hasMore && (
        <Button
          variant="secondary"
          onClick={() => {
            void c.fetchMoreUsers()
          }}
          disabled={c.loadingMore}
        >
          {c.loadingMore ? 'Loading…' : 'Load more'}
        </Button>
      )}

      <GrantRoleDialog
        user={c.grantingFor}
        open={c.grantingFor !== null}
        onClose={() => c.setGrantingFor(null)}
      />

      <RevokeRoleDialog
        revokingTarget={c.revokingTarget}
        submitting={c.submitting}
        onCancel={() => c.setRevokingTarget(null)}
        onConfirm={c.handleRevoke}
      />
    </div>
  )
}
