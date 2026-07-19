import {
  CONNECTION_TYPE_VALUES,
  type ConnectionHealthStatus,
  type ConnectionType,
} from '@/api/types/integrations'
import { useMemo } from 'react'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import { useConnectionsStore } from '@/stores/connections'
import type { ConnectionSortKey } from '@/stores/connections/types'
import { connectionTypeLabel } from './connection-fields'

const HEALTH_OPTIONS = [
  { value: '', label: 'All health' },
  { value: 'healthy', label: 'Healthy' },
  { value: 'degraded', label: 'Degraded' },
  { value: 'unhealthy', label: 'Unhealthy' },
  { value: 'unknown', label: 'Unknown' },
] as const

const VALID_HEALTH: ReadonlySet<string> = new Set([
  'healthy',
  'degraded',
  'unhealthy',
  'unknown',
])

const VALID_TYPES: ReadonlySet<string> = new Set(CONNECTION_TYPE_VALUES)

const SORT_OPTIONS = [
  { value: 'name', label: 'Name' },
  { value: 'type', label: 'Type' },
  { value: 'health', label: 'Health' },
  { value: 'created_at', label: 'Created' },
] as const

const VALID_SORT: ReadonlySet<string> = new Set([
  'name',
  'type',
  'health',
  'created_at',
])

export function ConnectionFilters() {
  const searchQuery = useConnectionsStore((s) => s.searchQuery)
  const typeFilter = useConnectionsStore((s) => s.typeFilter)
  const healthFilter = useConnectionsStore((s) => s.healthFilter)
  const sortBy = useConnectionsStore((s) => s.sortBy)
  const connectionTypes = useConnectionsStore((s) => s.connectionTypes)
  const setSearchQuery = useConnectionsStore((s) => s.setSearchQuery)
  const setTypeFilter = useConnectionsStore((s) => s.setTypeFilter)
  const setHealthFilter = useConnectionsStore((s) => s.setHealthFilter)
  const setSortBy = useConnectionsStore((s) => s.setSortBy)

  // Labels come from the backend registry (with a humanized fallback before it
  // loads); every valid type stays filterable regardless of load state.
  const typeOptions = useMemo<readonly { value: string; label: string }[]>(
    () => [
      { value: '', label: 'All types' },
      ...CONNECTION_TYPE_VALUES.map((t) => ({
        value: t,
        label: connectionTypeLabel(t, connectionTypes),
      })),
    ],
    [connectionTypes],
  )

  return (
    <div className="flex flex-wrap items-end gap-3">
      <InputField
        label="Search"
        placeholder="Search connections..."
        value={searchQuery}
        onChange={(e) => setSearchQuery(e.target.value)}
      />

      <SelectField
        label="Type"
        options={typeOptions}
        value={typeFilter ?? ''}
        onChange={(v) => {
          setTypeFilter(v && VALID_TYPES.has(v) ? (v as ConnectionType) : null)
        }}
      />

      <SelectField
        label="Health"
        options={HEALTH_OPTIONS}
        value={healthFilter ?? ''}
        onChange={(v) => {
          setHealthFilter(
            v && VALID_HEALTH.has(v) ? (v as ConnectionHealthStatus) : null,
          )
        }}
      />

      <SelectField
        label="Sort by"
        options={SORT_OPTIONS}
        value={sortBy}
        onChange={(v) => {
          if (VALID_SORT.has(v)) setSortBy(v as ConnectionSortKey)
        }}
      />
    </div>
  )
}
