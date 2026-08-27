import type { ReactNode } from 'react'
import { Package } from 'lucide-react'
import { EmptyState } from '@/components/ui/empty-state'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { ArtifactCard } from './ArtifactCard'
import type { Artifact } from '@/api/types/artifacts'

interface ArtifactGridViewProps {
  artifacts: readonly Artifact[]
  /**
   * What to show when there is nothing to render. The page supplies it,
   * because only the page knows whether the list is empty because a filter
   * excluded everything or because the deployment has no artifacts at all,
   * and telling an operator with no filters set to adjust their filters
   * names something they cannot act on.
   */
  emptyNode?: ReactNode
}

export function ArtifactGridView({ artifacts, emptyNode }: ArtifactGridViewProps) {
  if (artifacts.length === 0) {
    if (emptyNode !== undefined) return <>{emptyNode}</>
    return (
      <EmptyState
        icon={Package}
        title="No artifacts yet"
        description="Artifacts appear here as agents produce them: files, documents and deliverables the org has built."
      />
    )
  }

  return (
    <StaggerGroup className="grid grid-cols-1 gap-grid-gap sm:grid-cols-2 xl:grid-cols-3">
      {artifacts.map((artifact) => (
        <StaggerItem key={artifact.id}>
          <ArtifactCard artifact={artifact} />
        </StaggerItem>
      ))}
    </StaggerGroup>
  )
}
