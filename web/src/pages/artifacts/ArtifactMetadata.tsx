import { useState } from 'react'
import { useNavigate } from 'react-router'
import { Download, Trash2 } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import { MetadataGrid } from '@/components/ui/metadata-grid'
import { ContentTypeBadge } from '@/components/ui/content-type-badge'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { useArtifactsStore } from '@/stores/artifacts'
import { downloadArtifactFile } from '@/utils/download'
import { formatFileSize, formatDateTime, formatLabel } from '@/utils/format'
import { ROUTES } from '@/router/routes'
import type { Artifact } from '@/api/types/artifacts'

interface ArtifactMetadataProps {
  artifact: Artifact
}

export function ArtifactMetadata({ artifact }: ArtifactMetadataProps) {
  const navigate = useNavigate()
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const metadataItems = [
    { label: 'Type', value: formatLabel(artifact.type) },
    { label: 'Size', value: formatFileSize(artifact.size_bytes), valueClassName: 'font-mono text-xs' },
    {
      label: 'Content Type',
      value: artifact.content_type
        ? <ContentTypeBadge contentType={artifact.content_type} />
        : '--',
    },
    { label: 'Path', value: artifact.path, valueClassName: 'font-mono text-xs break-all' },
    { label: 'Task', value: artifact.task_id, valueClassName: 'font-mono text-xs' },
    { label: 'Project', value: artifact.project_id ?? '--', valueClassName: 'font-mono text-xs' },
    { label: 'Created By', value: artifact.created_by },
    { label: 'Created', value: formatDateTime(artifact.created_at) },
  ]

  function handleDownload() {
    void downloadArtifactFile(artifact.id, artifact.path.split('/').pop() || artifact.id)
  }

  async function handleDelete(): Promise<boolean> {
    setDeleting(true)
    try {
      const ok = await useArtifactsStore.getState().deleteArtifact(artifact.id)
      if (ok) {
        setDeleteOpen(false)
        void navigate(ROUTES.ARTIFACTS)
      }
      return ok
    } finally {
      // Always clear the in-flight flag so the UI stays responsive even
      // if the store ever throws (the sentinel contract says it should
      // not, but the finally keeps the primitive honest).
      setDeleting(false)
    }
  }

  return (
    <SectionCard
      title={artifact.path}
      action={
        <div className="flex gap-2">
          {artifact.size_bytes > 0 && (
            <Button variant="outline" size="sm" onClick={handleDownload}>
              <Download className="mr-1 size-4" />
              Download
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={() => setDeleteOpen(true)} disabled={deleting} className="text-danger hover:bg-danger/10">
            <Trash2 className="mr-1 size-4" />
            Delete
          </Button>
        </div>
      }
    >
      {artifact.description && (
        <p className="mb-4 text-sm text-muted-foreground">{artifact.description}</p>
      )}
      <MetadataGrid items={metadataItems} />

      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title="Delete Artifact"
        description="This will permanently delete the artifact and its stored content. This action cannot be undone."
        confirmLabel="Delete"
        variant="destructive"
        loading={deleting}
        onConfirm={handleDelete}
      />
    </SectionCard>
  )
}
