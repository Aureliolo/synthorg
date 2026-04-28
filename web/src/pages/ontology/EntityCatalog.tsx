/**
 * Entity catalog section -- card grid with filter tabs.
 */
import { ArrowDownAZ, ArrowUpAZ, Shapes } from 'lucide-react'
import { useMemo } from 'react'
import { useOntologyStore } from '@/stores/ontology'
import { SectionCard } from '@/components/ui/section-card'
import { SearchInput } from '@/components/ui/search-input'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { EmptyState } from '@/components/ui/empty-state'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { EntityCard } from './EntityCard'
import type { EntityResponse } from '@/api/endpoints/ontology'
import type { EntitySortKey } from '@/stores/ontology'

const TIER_OPTIONS = [
  { value: 'all' as const, label: 'All' },
  { value: 'core' as const, label: 'Core' },
  { value: 'user' as const, label: 'User' },
]

const SORT_OPTIONS: ReadonlyArray<{ value: EntitySortKey; label: string }> = [
  { value: 'name', label: 'Name' },
  { value: 'tier', label: 'Tier' },
  { value: 'attribute_count', label: 'Attributes' },
]

interface EntityCatalogProps {
  entities: readonly EntityResponse[]
}

function sortEntities(
  entities: readonly EntityResponse[],
  key: EntitySortKey,
  direction: 'asc' | 'desc',
): readonly EntityResponse[] {
  const sign = direction === 'asc' ? 1 : -1
  const cmp = (a: EntityResponse, b: EntityResponse): number => {
    if (key === 'name') return a.name.localeCompare(b.name) * sign
    if (key === 'tier') return a.tier.localeCompare(b.tier) * sign
    // ``attribute_count`` may not be a direct field on the response;
    // fall back to attributes.length when present, then break ties
    // by name to keep ordering stable across re-fetches.
    const aCount = (a as unknown as { attributes?: readonly unknown[] }).attributes?.length ?? 0
    const bCount = (b as unknown as { attributes?: readonly unknown[] }).attributes?.length ?? 0
    if (aCount === bCount) return a.name.localeCompare(b.name) * sign
    return (aCount - bCount) * sign
  }
  return [...entities].sort(cmp)
}

export function EntityCatalog({ entities }: EntityCatalogProps) {
  const tierFilter = useOntologyStore((s) => s.tierFilter)
  const searchQuery = useOntologyStore((s) => s.searchQuery)
  const sortBy = useOntologyStore((s) => s.entitySortBy)
  const sortDirection = useOntologyStore((s) => s.entitySortDirection)
  const setTierFilter = useOntologyStore((s) => s.setTierFilter)
  const setSearchQuery = useOntologyStore((s) => s.setSearchQuery)
  const setEntitySort = useOntologyStore((s) => s.setEntitySort)
  const setSelectedEntity = useOntologyStore((s) => s.setSelectedEntity)

  const hasActiveFilters = searchQuery.trim().length > 0 || tierFilter !== 'all'

  const sortedEntities = useMemo(
    () => sortEntities(entities, sortBy, sortDirection),
    [entities, sortBy, sortDirection],
  )

  return (
    <SectionCard title="Entity Catalog" icon={Shapes}>
      {/* Filters + sort */}
      <div className="flex flex-col gap-grid-gap sm:flex-row sm:items-center sm:justify-between">
        <SegmentedControl
          label="Filter by tier"
          value={tierFilter}
          onChange={setTierFilter}
          options={TIER_OPTIONS}
          size="sm"
        />

        <div className="flex flex-col gap-grid-gap sm:flex-row sm:items-center">
          <SegmentedControl
            label="Sort entities by"
            value={sortBy}
            onChange={(next) => setEntitySort(next)}
            options={SORT_OPTIONS}
            size="sm"
          />
          <button
            type="button"
            onClick={() => setEntitySort(sortBy)}
            aria-label={
              sortDirection === 'asc'
                ? 'Sort descending'
                : 'Sort ascending'
            }
            className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-border bg-surface text-text-secondary transition-colors hover:text-foreground"
          >
            {sortDirection === 'asc' ? (
              <ArrowDownAZ aria-hidden="true" className="size-4" />
            ) : (
              <ArrowUpAZ aria-hidden="true" className="size-4" />
            )}
          </button>

          <SearchInput
            value={searchQuery}
            onChange={(value) => setSearchQuery(value)}
            placeholder="Search entities..."
            aria-label="Search entities"
            maxWidth="narrow"
          />
        </div>
      </div>

      {/* Card grid */}
      {sortedEntities.length === 0 ? (
        <EmptyState
          icon={Shapes}
          title="No entities found"
          description={
            hasActiveFilters
              ? 'Try adjusting your search or filter criteria.'
              : 'Entity definitions will appear here once registered.'
          }
          action={
            hasActiveFilters
              ? {
                  label: 'Clear filters',
                  onClick: () => {
                    setSearchQuery('')
                    setTierFilter('all')
                  },
                  variant: 'outline',
                }
              : undefined
          }
        />
      ) : (
        <StaggerGroup className="grid grid-cols-1 gap-grid-gap sm:grid-cols-2 lg:grid-cols-3">
          {sortedEntities.map((entity) => (
            <StaggerItem key={entity.name}>
              <EntityCard
                entity={entity}
                onClick={() => setSelectedEntity(entity)}
              />
            </StaggerItem>
          ))}
        </StaggerGroup>
      )}
    </SectionCard>
  )
}
