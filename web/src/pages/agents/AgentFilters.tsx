import { useMemo } from 'react'
import { Search } from 'lucide-react'
import { useAgentsStore } from '@/stores/agents'
import { useCompanyStore } from '@/stores/company'
import {
  AGENT_STATUS_VALUES,
  SENIORITY_LEVEL_VALUES,
  type AgentStatus,
  type SeniorityLevel,
} from '@/api/types/enums'
import { formatLabel } from '@/utils/format'
import { cn } from '@/lib/utils'
import type { AgentSortKey } from '@/utils/agents'

const VALID_LEVELS = new Set<string>(SENIORITY_LEVEL_VALUES)
const VALID_STATUSES = new Set<string>(AGENT_STATUS_VALUES)
const VALID_SORT_KEYS = new Set<string>(['name', 'department', 'level', 'status', 'hiring_date'])

export function AgentFilters({ className }: { className?: string }) {
  const searchQuery = useAgentsStore((s) => s.searchQuery)
  const departmentFilter = useAgentsStore((s) => s.departmentFilter)
  const levelFilter = useAgentsStore((s) => s.levelFilter)
  const statusFilter = useAgentsStore((s) => s.statusFilter)
  const sortBy = useAgentsStore((s) => s.sortBy)

  // Department list comes from the LIVE company config, not the
  // hardcoded `DEPARTMENT_NAME_VALUES` enum.  Users create their
  // own departments via the setup wizard / packs, and the filter
  // dropdown needs to match what they actually have -- not a
  // static list of every enum member we support.
  const configDepartments = useCompanyStore((s) => s.config?.departments)
  const departmentOptions = useMemo<
    ReadonlyArray<{ value: string; label: string }>
  >(() => {
    if (!configDepartments || configDepartments.length === 0) return []
    return configDepartments.map((d) => ({
      value: d.name,
      label: d.display_name ?? formatLabel(d.name),
    }))
  }, [configDepartments])
  const validDepartmentNames = useMemo(
    () => new Set<string>(departmentOptions.map((o) => o.value)),
    [departmentOptions],
  )

  const setSearchQuery = useAgentsStore((s) => s.setSearchQuery)
  const setDepartmentFilter = useAgentsStore((s) => s.setDepartmentFilter)
  const setLevelFilter = useAgentsStore((s) => s.setLevelFilter)
  const setStatusFilter = useAgentsStore((s) => s.setStatusFilter)
  const setSortBy = useAgentsStore((s) => s.setSortBy)

  return (
    <div className={cn('flex flex-wrap items-center gap-3', className)}>
      {/* Search */}
      <div className="relative flex-1 min-w-48 max-w-sm">
        <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
        <input
          type="text"
          placeholder="Search by name or role..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="h-9 w-full rounded-lg border border-border bg-card pl-9 pr-3 text-sm text-foreground placeholder:text-muted-foreground focus:border-accent focus:outline-none"
          aria-label="Search agents"
        />
      </div>

      {/* Department -- list comes from the live company config */}
      <select
        value={departmentFilter ?? ''}
        onChange={(e) => {
          const v = e.target.value
          // Departments come from the LIVE company config (see the
          // useMemo above). The store's `departmentFilter` is
          // `string | null` precisely so user-created departments
          // beyond the static `DepartmentName` enum are accepted;
          // no cast needed.
          setDepartmentFilter(validDepartmentNames.has(v) ? v : null)
        }}
        className="h-9 rounded-lg border border-border bg-card px-3 text-sm text-foreground focus:border-accent focus:outline-none"
        aria-label="Filter by department"
      >
        <option value="">All departments</option>
        {departmentOptions.map((d) => (
          <option key={d.value} value={d.value}>{d.label}</option>
        ))}
      </select>

      {/* Level */}
      <select
        value={levelFilter ?? ''}
        onChange={(e) => {
          const v = e.target.value
          setLevelFilter(v && VALID_LEVELS.has(v) ? v as SeniorityLevel : null)
        }}
        className="h-9 rounded-lg border border-border bg-card px-3 text-sm text-foreground focus:border-accent focus:outline-none"
        aria-label="Filter by level"
      >
        <option value="">All levels</option>
        {SENIORITY_LEVEL_VALUES.map((l) => (
          <option key={l} value={l}>{formatLabel(l)}</option>
        ))}
      </select>

      {/* Status */}
      <select
        value={statusFilter ?? ''}
        onChange={(e) => {
          const v = e.target.value
          setStatusFilter(v && VALID_STATUSES.has(v) ? v as AgentStatus : null)
        }}
        className="h-9 rounded-lg border border-border bg-card px-3 text-sm text-foreground focus:border-accent focus:outline-none"
        aria-label="Filter by status"
      >
        <option value="">All statuses</option>
        {AGENT_STATUS_VALUES.map((s) => (
          <option key={s} value={s}>{formatLabel(s)}</option>
        ))}
      </select>

      {/* Sort */}
      <select
        value={sortBy}
        onChange={(e) => {
          const v = e.target.value
          if (VALID_SORT_KEYS.has(v)) setSortBy(v as AgentSortKey)
        }}
        className="h-9 rounded-lg border border-border bg-card px-3 text-sm text-foreground focus:border-accent focus:outline-none"
        aria-label="Sort agents by"
      >
        <option value="name">Sort: Name</option>
        <option value="department">Sort: Department</option>
        <option value="level">Sort: Level</option>
        <option value="status">Sort: Status</option>
        <option value="hiring_date">Sort: Hire date</option>
      </select>
    </div>
  )
}
