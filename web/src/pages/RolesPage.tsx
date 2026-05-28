/**
 * Roles overview.
 *
 * Enumerates the distinct role definitions in the organisation and
 * links each to its version history. The backend has no `GET /roles`
 * collection endpoint, so the role set is derived from the company
 * structure (the only enumeration source): a role with zero assigned
 * agents will not appear.
 */
import { useEffect, useMemo } from 'react'
import { Link } from 'react-router'
import { Briefcase } from 'lucide-react'
import { useCompanyStore } from '@/stores/company'
import { ListHeader } from '@/components/ui/list-header'
import { SectionCard } from '@/components/ui/section-card'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { Skeleton } from '@/components/ui/skeleton'
import { StatPill } from '@/components/ui/stat-pill'
import { ROUTES } from '@/router/routes'
import type { AgentConfig } from '@/api/types/agents'

interface RoleSummary {
  name: string
  agentCount: number
}

function deriveRoles(agents: readonly AgentConfig[]): RoleSummary[] {
  const counts = new Map<string, number>()
  for (const agent of agents) {
    const role = agent.role.trim()
    if (!role) continue
    counts.set(role, (counts.get(role) ?? 0) + 1)
  }
  return [...counts.entries()]
    .map(([name, agentCount]) => ({ name, agentCount }))
    .sort((a, b) => a.name.localeCompare(b.name))
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

  const roles = useMemo(() => deriveRoles(config?.agents ?? []), [config])

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

      <RolesBody roles={roles} loading={loading} error={error} />
    </div>
  )
}

interface RolesBodyProps {
  roles: readonly RoleSummary[]
  loading: boolean
  error: string | null
}

function RolesBody({ roles, loading, error }: RolesBodyProps) {
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
        title="No roles defined"
        description="Roles appear here once agents are assigned to them in the org structure."
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
