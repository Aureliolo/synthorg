/**
 * Roles overview.
 *
 * Enumerates the distinct role definitions in the organisation and
 * links each to its version history. The backend has no `GET /roles`
 * collection endpoint, so the role set is derived from the company
 * structure (the only enumeration source): a role with zero assigned
 * agents will not appear.
 */
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { Briefcase } from 'lucide-react'
import { useCompanyStore } from '@/stores/company'
import { ListHeader } from '@/components/ui/list-header'
import { SectionCard } from '@/components/ui/section-card'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { SearchInput } from '@/components/ui/search-input'
import { SelectField } from '@/components/ui/select-field'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { Skeleton } from '@/components/ui/skeleton'
import { StatPill } from '@/components/ui/stat-pill'
import { ROUTES } from '@/router/routes'
import type { AgentConfig } from '@/api/types/agents'
import { makeEnumParser } from '@/utils/type-guards'

interface RoleSummary {
  name: string
  agentCount: number
}

type RoleSortKey = 'name' | 'count'

const ROLE_SORT_OPTIONS: ReadonlyArray<{ value: RoleSortKey; label: string }> = [
  { value: 'name', label: 'Sort: Name' },
  { value: 'count', label: 'Sort: Agent count' },
]

const parseRoleSortKey = makeEnumParser<RoleSortKey>(ROLE_SORT_OPTIONS.map((o) => o.value))

function deriveRoles(agents: readonly AgentConfig[], sortBy: RoleSortKey): RoleSummary[] {
  const counts = new Map<string, number>()
  for (const agent of agents) {
    const role = agent.role.trim()
    if (!role) continue
    counts.set(role, (counts.get(role) ?? 0) + 1)
  }
  const roles = [...counts.entries()].map(([name, agentCount]) => ({ name, agentCount }))
  // Agent-count sort is descending (most-staffed first) with a name
  // tiebreaker so equal counts stay stably ordered.
  return sortBy === 'count'
    ? roles.sort((a, b) => b.agentCount - a.agentCount || a.name.localeCompare(b.name))
    : roles.sort((a, b) => a.name.localeCompare(b.name))
}

export default function RolesPage() {
  const config = useCompanyStore((s) => s.config)
  const loading = useCompanyStore((s) => s.loading)
  const error = useCompanyStore((s) => s.error)

  useEffect(() => {
    if (!useCompanyStore.getState().config) {
      void useCompanyStore.getState().fetchCompanyData()
    }
  }, [])

  const [searchQuery, setSearchQuery] = useState('')
  const [sortBy, setSortBy] = useState<RoleSortKey>('name')

  // Filtering folds into the same memo that derives roles, so a query over a
  // large org is a single cheap pass.
  const roles = useMemo(() => {
    const all = deriveRoles(config?.agents ?? [], sortBy)
    const query = searchQuery.trim().toLowerCase()
    return query ? all.filter((r) => r.name.toLowerCase().includes(query)) : all
  }, [config, searchQuery, sortBy])

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Roles"
        description="Job definitions across the organisation. Open a role to review its version history."
        count={roles.length}
      />

      {error && (
        <ErrorBanner severity="error" title="Could not load roles" description={error} />
      )}

      <SearchFilterSort
        search={
          <SearchInput
            value={searchQuery}
            onChange={setSearchQuery}
            placeholder="Search roles by name"
            ariaLabel="Search roles"
          />
        }
        sort={
          <SelectField
            label="Sort"
            value={sortBy}
            onChange={(value) => {
              const key = parseRoleSortKey(value)
              if (key) setSortBy(key)
            }}
            options={ROLE_SORT_OPTIONS}
          />
        }
      />

      <RolesBody roles={roles} loading={loading} error={error} searchActive={searchQuery.trim() !== ''} />
    </div>
  )
}

interface RolesBodyProps {
  roles: readonly RoleSummary[]
  loading: boolean
  error: string | null
  searchActive: boolean
}

function RolesBody({ roles, loading, error, searchActive }: RolesBodyProps) {
  if (loading && roles.length === 0) {
    return (
      <div className="flex flex-col gap-grid-gap">
        {[1, 2, 3, 4].map((i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    )
  }
  if (roles.length === 0) {
    if (error !== null) return null
    return (
      <EmptyState
        icon={Briefcase}
        title={searchActive ? 'No matching roles' : 'No roles defined'}
        description={
          searchActive
            ? 'Try a different search term or clear the field above.'
            : 'Roles appear here once agents are assigned to them in the org structure.'
        }
      />
    )
  }
  return (
    <SectionCard title="Roles" icon={Briefcase}>
      <ul className="flex flex-col divide-y divide-border">
        {roles.map((role) => (
          <RoleRow key={role.name} role={role} />
        ))}
      </ul>
    </SectionCard>
  )
}

function RoleRow({ role }: { role: RoleSummary }) {
  return (
    <li>
      <Link
        to={`${ROUTES.ROLES}/${encodeURIComponent(role.name)}/versions`}
        className="flex items-center justify-between gap-grid-gap px-card py-grid-gap transition-colors hover:bg-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <span className="text-sm font-medium text-foreground">{role.name}</span>
        <StatPill label="Agents" value={role.agentCount} />
      </Link>
    </li>
  )
}
