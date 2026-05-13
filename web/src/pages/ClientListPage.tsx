import { useCallback, useMemo, useState } from 'react'
import { Users } from 'lucide-react'
import { Link } from 'react-router'

import { SectionCard } from '@/components/ui/section-card'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { SearchInput } from '@/components/ui/search-input'
import { SkeletonCard } from '@/components/ui/skeleton'
import { useClientsData } from '@/hooks/useClientsData'
import { useEmptyStateProps } from '@/hooks/use-empty-state-props'
import { ROUTES } from '@/router/routes'
import { getLocale } from '@/utils/locale'

type ClientSortKey = 'name-asc' | 'name-desc' | 'strictness-asc' | 'strictness-desc'

const SORT_OPTIONS: ReadonlyArray<{ value: ClientSortKey; label: string }> = [
  { value: 'name-asc', label: 'Name (A-Z)' },
  { value: 'name-desc', label: 'Name (Z-A)' },
  { value: 'strictness-asc', label: 'Strictness (low to high)' },
  { value: 'strictness-desc', label: 'Strictness (high to low)' },
]

/**
 * Client pool list page.
 *
 * Surfaces every simulated client profile with a quick-link to the
 * detail page. Creation and editing happen via the REST API; this
 * page is read-only.
 */
export default function ClientListPage() {
  const { clients, loading, error, wsConnected } = useClientsData()
  const [searchQuery, setSearchQuery] = useState('')
  const [sortKey, setSortKey] = useState<ClientSortKey>('name-asc')

  const filteredClients = useMemo(() => {
    const trimmed = searchQuery.trim().toLowerCase()
    const matches = trimmed
      ? clients.filter(
          (c) =>
            c.name.toLowerCase().includes(trimmed) ||
            c.persona.toLowerCase().includes(trimmed),
        )
      : clients
    const locale = getLocale()
    return [...matches].sort((a, b) => {
      switch (sortKey) {
        case 'name-asc':
          return a.name.localeCompare(b.name, locale)
        case 'name-desc':
          return b.name.localeCompare(a.name, locale)
        case 'strictness-asc':
          return a.strictness_level - b.strictness_level
        case 'strictness-desc':
          return b.strictness_level - a.strictness_level
      }
    })
  }, [clients, searchQuery, sortKey])

  const handleSortChange = useCallback(
    (event: React.ChangeEvent<HTMLSelectElement>) => {
      setSortKey(event.target.value as ClientSortKey)
    },
    [],
  )

  // Hook before any early-return (rules-of-hooks): the loading
  // branch below short-circuits before the empty state matters.
  const emptyStateProps = useEmptyStateProps({
    filteredCount: filteredClients.length,
    totalCount: clients.length,
    filterActive: searchQuery.trim().length > 0,
    icon: Users,
    empty: {
      title: 'No clients yet',
      description:
        'Create simulated clients via the API to exercise the intake and review pipeline.',
    },
    filtered: {
      title: 'No matching clients',
      description: 'Try a different search term or clear the field above.',
      action: { label: 'Clear search', onClick: () => setSearchQuery('') },
    },
  })

  if (loading && clients.length === 0) {
    return (
      <div className="space-y-section-gap">
        <ListHeader title="Clients" />
        <div className="grid grid-cols-1 gap-grid-gap md:grid-cols-2 lg:grid-cols-3">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-section-gap">
      <ListHeader title="Clients" count={filteredClients.length} />

      {error && (
        <ErrorBanner severity="error" title="Could not load clients" description={error} />
      )}

      {!wsConnected && !loading && (
        <ErrorBanner
          variant="offline"
          title="Real-time updates disconnected"
          description="List refresh may be delayed until the connection recovers."
        />
      )}

      {clients.length > 0 && (
        <SearchFilterSort
          search={
            <SearchInput
              value={searchQuery}
              onChange={setSearchQuery}
              placeholder="Search clients by name or persona"
              ariaLabel="Search clients"
            />
          }
          sort={
            <label className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">Sort by</span>
              <select
                value={sortKey}
                onChange={handleSortChange}
                aria-label="Sort clients"
                className="rounded-md border border-border bg-background px-2 py-1 text-sm"
              >
                {SORT_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          }
        />
      )}

      {emptyStateProps ? (
        <EmptyState {...emptyStateProps} />
      ) : (
        <div className="grid grid-cols-1 gap-grid-gap md:grid-cols-2 lg:grid-cols-3">
          {filteredClients.map((client) => (
            <SectionCard
              key={client.client_id}
              title={client.name}
              icon={Users}
            >
              <div className="space-y-2 text-sm">
                <p className="text-text-secondary">{client.persona}</p>
                <p className="text-text-secondary">
                  <span className="font-medium text-foreground">Strictness:</span>{' '}
                  {client.strictness_level.toFixed(2)}
                </p>
                {client.expertise_domains.length > 0 && (
                  <p className="text-text-secondary">
                    <span className="font-medium text-foreground">Domains:</span>{' '}
                    {client.expertise_domains.join(', ')}
                  </p>
                )}
                <Link
                  to={ROUTES.CLIENT_DETAIL.replace(':clientId', encodeURIComponent(client.client_id))}
                  className="inline-block pt-2 text-accent hover:underline"
                >
                  View details →
                </Link>
              </div>
            </SectionCard>
          ))}
        </div>
      )}
    </div>
  )
}
