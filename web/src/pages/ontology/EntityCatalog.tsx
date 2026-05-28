/**
 * Entity catalog section: card grid with filter tabs.
 */
import { ArrowDownAZ, ArrowUpAZ, Shapes } from 'lucide-react'
import { useMemo } from 'react'
import { useOntologyStore } from '@/stores/ontology'
import { Button } from '@/components/ui/button'
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
    const countA = readAttributeCount(a)
    const countB = readAttributeCount(b)
    if (countA === countB) return a.name.localeCompare(b.name) * sign
    return (countA - countB) * sign
  }
  return [...entities].sort(cmp)
}

// `attribute_count` is the canonical scalar on summary list payloads; the
// `attributes` array is only present on detail responses. Read both so the
// comparator works against either shape.
function readAttributeCount(entity: EntityResponse): number {
  const summary = (entity as unknown as { attribute_count?: number }).attribute_count
  if (typeof summary === 'number') return summary
  const detail = (entity as unknown as { attributes?: readonly unknown[] }).attributes
  return detail?.length ?? 0
}

interface EntityCatalogControlsProps {
  tierFilter: 'all' | 'core' | 'user'
  searchQuery: string
  sortBy: EntitySortKey
  sortDirection: 'asc' | 'desc'
  onTierFilterChange: (value: 'all' | 'core' | 'user') => void
  onSearchQueryChange: (value: string) => void
  onSortChange: (key: EntitySortKey) => void
}

function EntityCatalogControls({
  tierFilter,
  searchQuery,
  sortBy,
  sortDirection,
  onTierFilterChange,
  onSearchQueryChange,
  onSortChange,
}: EntityCatalogControlsProps) {
  return (
    <div className="flex flex-col gap-grid-gap sm:flex-row sm:items-center sm:justify-between">
      <SegmentedControl
        label="Filter by tier"
        value={tierFilter}
        onChange={onTierFilterChange}
        options={TIER_OPTIONS}
        size="sm"
      />

      <div className="flex flex-col gap-grid-gap sm:flex-row sm:items-center">
        <SegmentedControl
          label="Sort entities by"
          value={sortBy}
          onChange={onSortChange}
          options={SORT_OPTIONS}
          size="sm"
        />
        <Button
          type="button"
          variant="outline"
          size="icon"
          onClick={() => onSortChange(sortBy)}
          aria-label={sortDirection === 'asc' ? 'Sort descending' : 'Sort ascending'}
        >
          {sortDirection === 'asc' ? (
            <ArrowDownAZ aria-hidden="true" className="size-4" />
          ) : (
            <ArrowUpAZ aria-hidden="true" className="size-4" />
          )}
        </Button>

        <SearchInput
          value={searchQuery}
          onChange={onSearchQueryChange}
          placeholder="Search entities..."
          aria-label="Search entities"
          maxWidth="narrow"
        />
      </div>
    </div>
  )
}

interface EntityCatalogEmptyProps {
  hasActiveFilters: boolean
  onClearFilters: () => void
}

function EntityCatalogEmpty({ hasActiveFilters, onClearFilters }: EntityCatalogEmptyProps) {
  return (
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
          ? { label: 'Clear filters', onClick: onClearFilters, variant: 'outline' }
          : undefined
      }
    />
  )
}

interface EntityCatalogGridProps {
  entities: readonly EntityResponse[]
  onSelect: (entity: EntityResponse) => void
  onDelete: (name: string) => boolean | void | Promise<boolean | void>
}

function EntityCatalogGrid({ entities, onSelect, onDelete }: EntityCatalogGridProps) {
  return (
    <StaggerGroup className="grid grid-cols-1 gap-grid-gap sm:grid-cols-2 lg:grid-cols-3">
      {entities.map((entity) => (
        <StaggerItem key={entity.name}>
          <EntityCard
            entity={entity}
            onClick={() => onSelect(entity)}
            onDelete={onDelete}
          />
        </StaggerItem>
      ))}
    </StaggerGroup>
  )
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
  const deleteEntity = useOntologyStore((s) => s.deleteEntity)

  const hasActiveFilters = searchQuery.trim().length > 0 || tierFilter !== 'all'

  const sortedEntities = useMemo(
    () => sortEntities(entities, sortBy, sortDirection),
    [entities, sortBy, sortDirection],
  )

  const handleClearFilters = () => {
    setSearchQuery('')
    setTierFilter('all')
  }

  return (
    <SectionCard title="Entity Catalog" icon={Shapes}>
      <EntityCatalogControls
        tierFilter={tierFilter}
        searchQuery={searchQuery}
        sortBy={sortBy}
        sortDirection={sortDirection}
        onTierFilterChange={setTierFilter}
        onSearchQueryChange={setSearchQuery}
        onSortChange={setEntitySort}
      />

      {sortedEntities.length === 0 ? (
        <EntityCatalogEmpty hasActiveFilters={hasActiveFilters} onClearFilters={handleClearFilters} />
      ) : (
        <EntityCatalogGrid
          entities={sortedEntities}
          onSelect={setSelectedEntity}
          onDelete={deleteEntity}
        />
      )}
    </SectionCard>
  )
}
