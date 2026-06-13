/**
 * Ontology page -- entity catalog + drift monitor.
 */
import { Shapes } from 'lucide-react'
import { useOntologyData } from '@/hooks/useOntologyData'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ListHeader } from '@/components/ui/list-header'
import { EntityCatalog } from './ontology/EntityCatalog'
import { DriftMonitor } from './ontology/DriftMonitor'
import { OntologyAdminSection } from './ontology/OntologyAdminSection'
import { OntologySkeleton } from './ontology/OntologySkeleton'

export default function OntologyPage() {
  const {
    filteredEntities,
    totalEntities,
    entitiesLoading,
    entitiesError,
    driftReports,
    driftLoading,
    driftError,
    coreCount,
    userCount,
  } = useOntologyData()

  if (entitiesLoading && totalEntities === 0) {
    return <OntologySkeleton />
  }

  const header = (
    <ListHeader
      title="Ontology"
      description="Entity definitions, versioning, and semantic drift monitoring"
      count={totalEntities}
      countLabel={`${totalEntities} entities (${coreCount} core, ${userCount} user)`}
    />
  )

  // Truly-empty ontology (no data, no filters) -- skip both EntityCatalog
  // and DriftMonitor and show a single page-level empty state.
  if (totalEntities === 0 && !entitiesLoading && !entitiesError) {
    return (
      <div className="space-y-section-gap">
        {header}
        <EmptyState
          icon={Shapes}
          title="No entities registered"
          description="Your ontology is empty. Entities appear once your agents register them or you define them via the API."
        />
      </div>
    )
  }

  return (
    <div className="space-y-section-gap">
      {header}

      {/* Error alert */}
      {entitiesError && (
        <ErrorBanner severity="error" title="Could not load ontology" description={entitiesError} />
      )}

      {/* Entity Catalog */}
      <ErrorBoundary level="section">
        <EntityCatalog entities={filteredEntities} />
      </ErrorBoundary>

      {/* Drift Monitor */}
      <ErrorBoundary level="section">
        <DriftMonitor
          reports={driftReports}
          loading={driftLoading}
          error={driftError}
        />
      </ErrorBoundary>

      {/* Admin actions */}
      <ErrorBoundary level="section">
        <OntologyAdminSection />
      </ErrorBoundary>
    </div>
  )
}
