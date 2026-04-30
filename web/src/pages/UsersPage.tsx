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
import { useEffect, useMemo, useState } from 'react'
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
import type { OrgRole } from '@/api/types/enums'
import type { UserResponse } from '@/api/endpoints/users'
import { GrantRoleDialog } from './users/GrantRoleDialog'
import { cn } from '@/lib/utils'

const ROLE_PILL_CLASS: Record<OrgRole, string> = {
  owner: 'bg-accent/10 text-accent border-accent/20',
  department_admin: 'bg-warning/10 text-warning border-warning/20',
  editor: 'bg-info/10 text-info border-info/20',
  viewer: 'bg-surface text-text-secondary border-border',
}

function RolePill({
  role,
  scopedDepartments,
  onRevoke,
  busy,
}: {
  role: OrgRole
  scopedDepartments?: readonly string[]
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
        ROLE_PILL_CLASS[role],
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

export default function UsersPage() {
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
  const [revokingTarget, setRevokingTarget] = useState<{
    user: UserResponse
    role: OrgRole
  } | null>(null)
  const [searchQuery, setSearchQuery] = useState('')

  useEffect(() => {
    void fetchUsers()
  }, [fetchUsers])

  const sortedUsers = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    const filtered = q
      ? users.filter(
          (u) =>
            u.username.toLowerCase().includes(q) ||
            u.role.toLowerCase().includes(q),
        )
      : users
    return [...filtered].sort((a, b) => a.username.localeCompare(b.username))
  }, [users, searchQuery])

  return (
    <div className="flex flex-col gap-section-gap">
      <ListHeader title="Users" count={sortedUsers.length} />

      <SearchFilterSort
        search={
          <SearchInput
            value={searchQuery}
            onChange={setSearchQuery}
            placeholder="Search users by name or role"
            ariaLabel="Search users"
          />
        }
      />

      {error && (
        <ErrorBanner
          severity="error"
          title="Could not load users"
          description={error}
          onRetry={() => {
            void fetchUsers()
          }}
        />
      )}

      {loading && users.length === 0 ? (
        <div className="flex flex-col gap-grid-gap">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-20 w-full" />
          ))}
        </div>
      ) : sortedUsers.length === 0 ? (
        // Use ``sortedUsers`` (the post-search-filter view) so an
        // active query that yields nothing renders a search-empty
        // message instead of a blank list. ``users.length === 0``
        // would only fire on the truly-empty roster case and was
        // hiding the "no matches" state behind a search.
        <EmptyState
          title={searchQuery ? 'No matching users' : 'No users'}
          description={
            searchQuery
              ? 'Try a different search term or clear the field above.'
              : "Human users with dashboard access will appear here once they're provisioned."
          }
        />
      ) : (
        <ul className="flex flex-col gap-grid-gap">
          {sortedUsers.map((user) => (
            <li key={user.id}>
              <SectionCard
                title={user.username}
                icon={ShieldCheck}
                action={
                  <Button
                    variant="secondary"
                    onClick={() => setGrantingFor(user)}
                  >
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
                    <dd className="text-foreground">
                      {formatDateTime(user.created_at)}
                    </dd>
                  </div>
                  <div className="sm:col-span-2">
                    <dt className="text-text-secondary">Org roles</dt>
                    <dd className="mt-1 flex flex-wrap gap-1">
                      {user.org_roles.length === 0 && (
                        <span className="text-text-secondary">
                          None granted
                        </span>
                      )}
                      {user.org_roles.map((role) => (
                        <RolePill
                          key={role}
                          role={role}
                          scopedDepartments={
                            role === 'department_admin'
                              ? user.scoped_departments
                              : undefined
                          }
                          onRevoke={() =>
                            setRevokingTarget({ user, role })
                          }
                          busy={submitting}
                        />
                      ))}
                    </dd>
                  </div>
                </dl>
              </SectionCard>
            </li>
          ))}
        </ul>
      )}

      {hasMore && (
        <Button
          variant="secondary"
          onClick={() => {
            void fetchMoreUsers()
          }}
          disabled={loadingMore}
        >
          {loadingMore ? 'Loading…' : 'Load more'}
        </Button>
      )}

      <GrantRoleDialog
        user={grantingFor}
        open={grantingFor !== null}
        onClose={() => setGrantingFor(null)}
      />

      <ConfirmDialog
        open={revokingTarget !== null}
        onOpenChange={(next) => {
          if (!next) setRevokingTarget(null)
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
          if (!revokingTarget) {
            setRevokingTarget(null)
            return
          }
          const ok = await revokeOrgRole(
            revokingTarget.user.id,
            revokingTarget.role,
          )
          // Only dismiss on success; on failure the store has
          // already surfaced the error toast and we keep the
          // confirm dialog open so the user can retry in context.
          if (ok) setRevokingTarget(null)
        }}
      />
    </div>
  )
}
