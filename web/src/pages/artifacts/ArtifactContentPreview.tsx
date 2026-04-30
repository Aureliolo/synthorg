import { useCallback, useEffect, useRef, useState } from 'react'
import { Eye } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState } from '@/components/ui/empty-state'
import { LazyCodeMirrorEditor } from '@/components/ui/lazy-code-mirror-editor'
import { downloadArtifactContent } from '@/api/endpoints/artifacts'
import { downloadArtifactFile } from '@/utils/download'
import { getErrorMessage, isAxiosError } from '@/utils/errors'
import { createLogger } from '@/lib/logger'
import type { Artifact } from '@/api/types/artifacts'

const log = createLogger('ArtifactContentPreview')

interface ArtifactContentPreviewProps {
  artifact: Artifact
  contentPreview: string | null
}

function getLanguage(contentType: string): 'json' | 'yaml' {
  const lower = contentType.toLowerCase()
  if (lower === 'application/json') return 'json'
  if (lower === 'application/yaml' || lower === 'application/x-yaml' || lower === 'text/yaml') return 'yaml'
  // Falls back to JSON mode for non-JSON/non-YAML text types.
  // Plain text may show minor syntax coloring.
  return 'json'
}

const NOOP = () => {}

export function ArtifactContentPreview({ artifact, contentPreview }: ArtifactContentPreviewProps) {
  const [imageSrc, setImageSrc] = useState<string | null>(null)
  const [imageError, setImageError] = useState<string | null>(null)
  const imageSrcRef = useRef<string | null>(null)

  // Exclude SVG -- it is an XML document with JavaScript execution capability (XSS risk).
  const isImage = artifact.content_type?.startsWith('image/') && artifact.content_type !== 'image/svg+xml'
  const isText = contentPreview !== null

  const handleDownload = useCallback(() => {
    downloadArtifactFile(artifact.id, artifact.path.split('/').pop() || artifact.id)
  }, [artifact.id, artifact.path])

  // Load image as blob URL for image content types
  useEffect(() => {
    if (!isImage || artifact.size_bytes === 0) return
    let revoked = false
    downloadArtifactContent(artifact.id)
      .then((blob) => {
        if (revoked) return
        const url = URL.createObjectURL(blob)
        imageSrcRef.current = url
        setImageSrc(url)
      })
      .catch((err: unknown) => {
        if (revoked) return
        const message = getErrorMessage(err)
        // Structured log so an operator chasing missing previews can
        // tell whether this is a 404 (artifact gone), a 5xx (storage
        // backend issue), or a network failure. Don't include the
        // artifact path; it can carry user-content that might be
        // sensitive in logs.
        log.error('artifact image preview failed to load', {
          artifactId: artifact.id,
          contentType: artifact.content_type,
          statusCode: isAxiosError(err) ? err.response?.status ?? null : null,
          error: message,
        })
        setImageError(message)
      })
    return () => {
      revoked = true
      setImageSrc(null)
      setImageError(null)
      if (imageSrcRef.current) {
        URL.revokeObjectURL(imageSrcRef.current)
        imageSrcRef.current = null
      }
    }
  }, [artifact.id, isImage, artifact.size_bytes])

  if (artifact.size_bytes === 0) {
    return (
      <SectionCard title="Content">
        <EmptyState
          icon={Eye}
          title="No content uploaded"
          description="This artifact has no stored content."
        />
      </SectionCard>
    )
  }

  if (isText) {
    return (
      <SectionCard title="Content Preview">
        <LazyCodeMirrorEditor
          value={contentPreview}
          onChange={NOOP}
          language={getLanguage(artifact.content_type)}
          readOnly
        />
      </SectionCard>
    )
  }

  if (isImage && imageError) {
    return (
      <SectionCard title="Content Preview">
        <EmptyState
          icon={Eye}
          title="Image preview failed to load"
          description={imageError}
          action={{ label: 'Download', onClick: handleDownload }}
        />
      </SectionCard>
    )
  }

  if (isImage && !imageSrc && !imageError) {
    return (
      <SectionCard title="Content Preview">
        <Skeleton className="h-48 w-full rounded-md" />
      </SectionCard>
    )
  }

  if (isImage && imageSrc) {
    return (
      <SectionCard title="Content Preview">
        <img
          src={imageSrc}
          alt={`Preview of ${artifact.path}`}
          className="max-h-96 rounded-md border border-border object-contain"
        />
      </SectionCard>
    )
  }

  return (
    <SectionCard title="Content">
      <EmptyState
        icon={Eye}
        title="Preview not available"
        description={`Content type: ${artifact.content_type || 'unknown'}`}
        action={{ label: 'Download', onClick: handleDownload }}
      />
    </SectionCard>
  )
}
