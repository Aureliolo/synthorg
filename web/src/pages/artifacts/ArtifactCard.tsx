import { memo } from 'react'
import { Link } from 'react-router'
import { ROUTES } from '@/router/routes'
import { ContentTypeBadge } from '@/components/ui/content-type-badge'
import { StatPill } from '@/components/ui/stat-pill'
import { formatFileSize } from '@/utils/format'
import { formatRelativeTime, formatLabel, formatDateTime } from '@/utils/format'
import type { Artifact } from '@/api/types/artifacts'

interface ArtifactCardProps {
  artifact: Artifact
}

function ArtifactCardInner({ artifact }: ArtifactCardProps) {
  return (
    <Link
      to={ROUTES.ARTIFACT_DETAIL.replace(':artifactId', encodeURIComponent(artifact.id))}
      className="block rounded-lg border border-border bg-card p-card transition-all duration-[var(--so-transition-default)] hover:-translate-y-px hover:shadow-[var(--so-shadow-card-hover)]"
    >
      <div className="mb-2 truncate font-mono text-sm font-medium text-foreground">
        {artifact.path}
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <StatPill label="Type" value={formatLabel(artifact.type)} />
        {artifact.content_type && <ContentTypeBadge contentType={artifact.content_type} />}
      </div>

      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>{artifact.size_bytes > 0 ? formatFileSize(artifact.size_bytes) : 'No content'}</span>
        <time dateTime={artifact.created_at ?? undefined} title={formatDateTime(artifact.created_at)}>
          {formatRelativeTime(artifact.created_at)}
        </time>
      </div>

      <div className="mt-1 text-xs text-text-muted">
        by {artifact.created_by}
      </div>
    </Link>
  )
}

export const ArtifactCard = memo(ArtifactCardInner)
