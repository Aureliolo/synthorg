import { useParams } from 'react-router'
import { Breadcrumbs } from '@/components/ui/breadcrumbs'
import { DetailNavBar } from '@/components/ui/detail-nav-bar'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ROUTES } from '@/router/routes'

import { ArtifactDetailSkeleton } from './artifacts/ArtifactDetailSkeleton'
import { ArtifactMetadata } from './artifacts/ArtifactMetadata'
import { ArtifactContentPreview } from './artifacts/ArtifactContentPreview'
import { useArtifactDetailPageController } from './artifacts/useArtifactDetailPageController'

export default function ArtifactDetailPage() {
  const { artifactId } = useParams<{ artifactId: string }>()
  const ctrl = useArtifactDetailPageController(artifactId)

  if (ctrl.showErrorPage) {
    return (
      <div className="space-y-section-gap">
        <Breadcrumbs
          items={[
            { label: 'Artifacts', to: ROUTES.ARTIFACTS },
            { label: artifactId || 'Unknown artifact' },
          ]}
        />
        <ErrorBanner severity="error" title="Artifact not found" description={ctrl.error} />
      </div>
    )
  }

  if (ctrl.showSkeleton || !ctrl.artifact) return <ArtifactDetailSkeleton />

  return (
    <div className="space-y-section-gap">
      <div className="flex flex-wrap items-center gap-3">
        <Breadcrumbs
          items={[
            { label: 'Artifacts', to: ROUTES.ARTIFACTS },
            { label: ctrl.artifact.id },
          ]}
        />
        <DetailNavBar
          canPrev={ctrl.nav.canPrev}
          canNext={ctrl.nav.canNext}
          onPrev={ctrl.goPrev}
          onNext={ctrl.goNext}
          position={ctrl.nav.position}
        />
      </div>

      {ctrl.error && (
        <ErrorBanner
          severity="error"
          title="Could not load artifact"
          description={ctrl.error}
        />
      )}

      {ctrl.showOfflineBanner && (
        <ErrorBanner
          variant="offline"
          title="Real-time updates disconnected"
          description={ctrl.wsSetupError ?? 'Data may be stale until the connection recovers.'}
        />
      )}

      <ErrorBoundary level="section">
        <ArtifactMetadata artifact={ctrl.artifact} />
      </ErrorBoundary>

      <ErrorBoundary level="section">
        <ArtifactContentPreview
          artifact={ctrl.artifact}
          contentPreview={ctrl.contentPreview}
        />
      </ErrorBoundary>
    </div>
  )
}
