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
const VALID_SORT_KEYS = new Set<string>([
  'name',
  'department',
  'level',
  'status',
  'hiring_date',
])

const SELECT_CLASSES =
  'h-9 rounded-lg border border-border bg-card px-3 text-sm text-foreground focus:border-accent focus:outline-none'

export function AgentFilters({ className }: { className?: string }) {
  const searchQuery = useAgentsStore((s) => s.searchQuery)
  const departmentFilter = useAgentsStore((s) => s.departmentFilter)
  const levelFilter = useAgentsStore((s) => s.levelFilter)
  const statusFilter = useAgentsStore((s) => s.statusFilter)
  const sortBy = useAgentsStore((s) => s.sortBy)

  // Department list comes from the LIVE company config, not the hardcoded
  // ``DEPARTMENT_NAME_VALUES`` enum. Users create their own departments via
  // the setup wizard / packs, and the filter dropdown needs to match what
  // they actually have.
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
      <SearchField value={searchQuery} onValueChange={setSearchQuery} />
      <DepartmentSelect
        value={departmentFilter}
        options={departmentOptions}
        validNames={validDepartmentNames}
        onValueChange={setDepartmentFilter}
      />
      <LevelSelect value={levelFilter} onValueChange={setLevelFilter} />
      <StatusSelect value={statusFilter} onValueChange={setStatusFilter} />
      <SortSelect value={sortBy} onValueChange={setSortBy} />
    </div>
  )
}

interface SearchFieldProps {
  value: string
  onValueChange: (value: string) => void
}

function SearchField({ value, onValueChange }: SearchFieldProps) {
  return (
    <div className="relative flex-1 min-w-48 max-w-sm">
      <Search
        className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
        aria-hidden="true"
      />
      <input
        type="text"
        placeholder="Search by name or role..."
        value={value}
        onChange={(e) => onValueChange(e.target.value)}
        className="h-9 w-full rounded-lg border border-border bg-card pl-9 pr-3 text-sm text-foreground placeholder:text-muted-foreground focus:border-accent focus:outline-none"
        aria-label="Search agents"
      />
    </div>
  )
}

interface DepartmentSelectProps {
  value: string | null
  options: ReadonlyArray<{ value: string; label: string }>
  validNames: ReadonlySet<string>
  onValueChange: (value: string | null) => void
}

function DepartmentSelect({
  value,
  options,
  validNames,
  onValueChange,
}: DepartmentSelectProps) {
  return (
    <select
      value={value ?? ''}
      onChange={(e) => {
        const next = e.target.value
        // Departments come from the LIVE company config. The store's
        // ``departmentFilter`` is ``string | null`` precisely so user-created
        // departments beyond the static ``DepartmentName`` enum are accepted.
        onValueChange(validNames.has(next) ? next : null)
      }}
      className={SELECT_CLASSES}
      aria-label="Filter by department"
    >
      <option value="">All departments</option>
      {options.map((d) => (
        <option key={d.value} value={d.value}>
          {d.label}
        </option>
      ))}
    </select>
  )
}

interface LevelSelectProps {
  value: SeniorityLevel | null
  onValueChange: (value: SeniorityLevel | null) => void
}

function LevelSelect({ value, onValueChange }: LevelSelectProps) {
  return (
    <select
      value={value ?? ''}
      onChange={(e) => {
        const next = e.target.value
        onValueChange(next && VALID_LEVELS.has(next) ? (next as SeniorityLevel) : null)
      }}
      className={SELECT_CLASSES}
      aria-label="Filter by level"
    >
      <option value="">All levels</option>
      {SENIORITY_LEVEL_VALUES.map((l) => (
        <option key={l} value={l}>
          {formatLabel(l)}
        </option>
      ))}
    </select>
  )
}

interface StatusSelectProps {
  value: AgentStatus | null
  onValueChange: (value: AgentStatus | null) => void
}

function StatusSelect({ value, onValueChange }: StatusSelectProps) {
  return (
    <select
      value={value ?? ''}
      onChange={(e) => {
        const next = e.target.value
        onValueChange(next && VALID_STATUSES.has(next) ? (next as AgentStatus) : null)
      }}
      className={SELECT_CLASSES}
      aria-label="Filter by status"
    >
      <option value="">All statuses</option>
      {AGENT_STATUS_VALUES.map((s) => (
        <option key={s} value={s}>
          {formatLabel(s)}
        </option>
      ))}
    </select>
  )
}

interface SortSelectProps {
  value: AgentSortKey
  onValueChange: (value: AgentSortKey) => void
}

function SortSelect({ value, onValueChange }: SortSelectProps) {
  return (
    <select
      value={value}
      onChange={(e) => {
        const next = e.target.value
        if (VALID_SORT_KEYS.has(next)) onValueChange(next as AgentSortKey)
      }}
      className={SELECT_CLASSES}
      aria-label="Sort agents by"
    >
      <option value="name">Sort: Name</option>
      <option value="department">Sort: Department</option>
      <option value="level">Sort: Level</option>
      <option value="status">Sort: Status</option>
      <option value="hiring_date">Sort: Hire date</option>
    </select>
  )
}
