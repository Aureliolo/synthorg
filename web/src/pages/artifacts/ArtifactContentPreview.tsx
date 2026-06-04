import { useCallback } from 'react'
import { Eye } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState } from '@/components/ui/empty-state'
import { LazyCodeMirrorEditor } from '@/components/ui/lazy-code-mirror-editor'
import { downloadArtifactFile } from '@/utils/download'
import type { Artifact } from '@/api/types/artifacts'

import { useArtifactImagePreview } from './useArtifactImagePreview'

interface ArtifactContentPreviewProps {
  artifact: Artifact
  contentPreview: string | null
}

const NOOP = () => {}

type PreviewMode =
  | { kind: 'empty' }
  | { kind: 'text'; preview: string }
  | { kind: 'image-error'; message: string }
  | { kind: 'image-loading' }
  | { kind: 'image-ready'; src: string }
  | { kind: 'unsupported' }

export function ArtifactContentPreview({ artifact, contentPreview }: ArtifactContentPreviewProps) {
  // Exclude SVG: it is an XML document with JavaScript execution capability (XSS risk).
  const isImage =
    artifact.content_type?.startsWith('image/') &&
    artifact.content_type !== 'image/svg+xml'
  const { imageSrc, imageError } = useArtifactImagePreview(artifact, isImage)

  const handleDownload = useCallback(() => {
    void downloadArtifactFile(artifact.id, artifact.path.split('/').pop() || artifact.id)
  }, [artifact.id, artifact.path])

  const mode = derivePreviewMode(artifact, contentPreview, isImage, imageSrc, imageError)
  return renderPreviewMode(mode, artifact, handleDownload)
}

function renderPreviewMode(
  mode: PreviewMode,
  artifact: Artifact,
  handleDownload: () => void,
): React.ReactElement {
  if (mode.kind === 'empty') return <EmptyContent />
  if (mode.kind === 'text') {
    return <TextPreview preview={mode.preview} contentType={artifact.content_type ?? ''} />
  }
  if (mode.kind === 'image-error') {
    return <ImageErrorPreview message={mode.message} onDownload={handleDownload} />
  }
  if (mode.kind === 'image-loading') return <ImageLoadingPreview />
  if (mode.kind === 'image-ready') {
    return <ImagePreviewCard src={mode.src} alt={`Preview of ${artifact.path}`} />
  }
  return <UnsupportedPreview contentType={artifact.content_type} onDownload={handleDownload} />
}

function derivePreviewMode(
  artifact: Artifact,
  contentPreview: string | null,
  isImage: boolean,
  imageSrc: string | null,
  imageError: string | null,
): PreviewMode {
  if (artifact.size_bytes === 0) return { kind: 'empty' }
  if (contentPreview !== null) return { kind: 'text', preview: contentPreview }
  if (isImage && imageError) return { kind: 'image-error', message: imageError }
  if (isImage && imageSrc) return { kind: 'image-ready', src: imageSrc }
  if (isImage) return { kind: 'image-loading' }
  return { kind: 'unsupported' }
}

function getLanguage(contentType: string): 'json' | 'yaml' {
  const lower = contentType.toLowerCase()
  if (lower === 'application/json') return 'json'
  if (lower === 'application/yaml' || lower === 'application/x-yaml' || lower === 'text/yaml') {
    return 'yaml'
  }
  // Falls back to JSON mode for non-JSON/non-YAML text types.
  return 'json'
}

function EmptyContent() {
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

interface TextPreviewProps {
  preview: string
  contentType: string
}

function TextPreview({ preview, contentType }: TextPreviewProps) {
  return (
    <SectionCard title="Content Preview">
      <LazyCodeMirrorEditor
        value={preview}
        onChange={NOOP}
        language={getLanguage(contentType)}
        readOnly
      />
    </SectionCard>
  )
}

interface ImageErrorPreviewProps {
  message: string
  onDownload: () => void
}

function ImageErrorPreview({ message, onDownload }: ImageErrorPreviewProps) {
  return (
    <SectionCard title="Content Preview">
      <EmptyState
        icon={Eye}
        title="Image preview failed to load"
        description={message}
        action={{ label: 'Download', onClick: onDownload }}
      />
    </SectionCard>
  )
}

function ImageLoadingPreview() {
  return (
    <SectionCard title="Content Preview">
      <Skeleton className="h-48 w-full rounded-md" />
    </SectionCard>
  )
}

interface ImagePreviewCardProps {
  src: string
  alt: string
}

function ImagePreviewCard({ src, alt }: ImagePreviewCardProps) {
  return (
    <SectionCard title="Content Preview">
      {/* width / height attributes set so the browser reserves space before the image
          decodes; without them the layout shifts on first paint and Lighthouse flags
          the page as having a high CLS. The image is constrained by max-h-96 +
          object-contain so the intrinsic dimensions only steer aspect-ratio
          reservation, not final size. */}
      <img
        src={src}
        alt={alt}
        width={800}
        height={600}
        className="max-h-96 rounded-md border border-border object-contain"
      />
    </SectionCard>
  )
}

interface UnsupportedPreviewProps {
  contentType: string | null | undefined
  onDownload: () => void
}

function UnsupportedPreview({ contentType, onDownload }: UnsupportedPreviewProps) {
  return (
    <SectionCard title="Content">
      <EmptyState
        icon={Eye}
        title="Preview not available"
        description={`Content type: ${contentType || 'unknown'}`}
        action={{ label: 'Download', onClick: onDownload }}
      />
    </SectionCard>
  )
}
