import { Drawer } from '@/components/ui/drawer'
import { MetadataGrid } from '@/components/ui/metadata-grid'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Trash2 } from 'lucide-react'
import { useSubworkflowDetailDrawerData } from './useSubworkflowDetailDrawerData'
import type { ParentReference, SubworkflowSummary } from '@/api/types/workflows'

interface SubworkflowDetailDrawerProps {
  open: boolean
  onClose: () => void
  subworkflow: SubworkflowSummary | null
}

export function SubworkflowDetailDrawer({
  open,
  onClose,
  subworkflow,
}: SubworkflowDetailDrawerProps) {
  const data = useSubworkflowDetailDrawerData(open, subworkflow, onClose)
  if (!subworkflow) return null

  const deleteDisabled =
    data.loading || data.parents.length > 0 || !data.detailsLoaded
  const deleteTooltip = deriveDeleteTooltip(data.loading, data.detailsLoaded, data.parents.length)

  return (
    <>
      <Drawer
        open={open}
        onClose={onClose}
        side="right"
        title={subworkflow.name}
        ariaLabel={`Subworkflow details: ${subworkflow.name}`}
      >
        <div className="flex flex-col gap-section-gap">
          <SubworkflowMetadata subworkflow={subworkflow} />
          <VersionsSection loading={data.loading} versions={data.versions} />
          <ParentsSection loading={data.loading} parents={data.parents} />
          <div className="pt-2">
            <Button
              variant="destructive"
              size="sm"
              onClick={data.openDeleteConfirm}
              disabled={deleteDisabled}
              title={deleteTooltip}
            >
              <Trash2 className="mr-1 size-3.5" />
              Delete Latest Version
            </Button>
          </div>
        </div>
      </Drawer>

      <ConfirmDialog
        open={data.deleteConfirmOpen}
        onOpenChange={(next) => {
          if (!next) data.closeDeleteConfirm()
        }}
        onConfirm={data.handleDelete}
        title="Delete Subworkflow"
        description={`Delete ${subworkflow.name} v${subworkflow.latest_version}? This cannot be undone.`}
        confirmLabel="Delete"
        variant="destructive"
        loading={data.deleting}
      />
    </>
  )
}

function deriveDeleteTooltip(
  loading: boolean,
  detailsLoaded: boolean,
  parentsCount: number,
): string {
  if (loading) return 'Checking parent references...'
  if (!detailsLoaded) return 'Details out of date. Refresh to enable delete.'
  if (parentsCount > 0) return 'Cannot delete: still referenced'
  return 'Delete this subworkflow version'
}

interface SubworkflowMetadataProps {
  subworkflow: SubworkflowSummary
}

function SubworkflowMetadata({ subworkflow }: SubworkflowMetadataProps) {
  return (
    <MetadataGrid
      items={[
        { label: 'ID', value: subworkflow.subworkflow_id },
        { label: 'Latest Version', value: subworkflow.latest_version },
        {
          label: 'I/O',
          value: `${subworkflow.input_count} inputs, ${subworkflow.output_count} outputs`,
        },
      ]}
    />
  )
}

interface VersionsSectionProps {
  loading: boolean
  versions: readonly string[]
}

function VersionsSection({ loading, versions }: VersionsSectionProps) {
  return (
    <div>
      <h3 className="mb-2 text-sm font-medium text-foreground">
        Versions ({versions.length})
      </h3>
      {loading ? (
        <div className="flex flex-col gap-1" role="status" aria-label="Loading versions">
          <Skeleton className="h-6 rounded" />
          <Skeleton className="h-6 rounded" />
        </div>
      ) : (
        <ul className="flex flex-col gap-1">
          {versions.map((v) => (
            <li
              key={v}
              className="rounded-md bg-accent/5 px-2 py-1 text-xs text-foreground"
            >
              v{v}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

interface ParentsSectionProps {
  loading: boolean
  parents: readonly ParentReference[]
}

function ParentsSection({ loading, parents }: ParentsSectionProps) {
  return (
    <div>
      <h3 className="mb-2 text-sm font-medium text-foreground">
        Parents ({parents.length})
      </h3>
      {loading ? (
        <Skeleton className="h-12 rounded" />
      ) : parents.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          No parent references for this subworkflow.
        </p>
      ) : (
        <ul className="flex flex-col gap-1">
          {parents.map((p) => (
            <li
              key={`${p.parent_id}-${p.node_id}`}
              className="rounded-md border border-border px-2 py-1 text-xs"
            >
              <span className="font-medium text-foreground">{p.parent_name}</span>
              <span className="ml-1 text-muted-foreground">
                (v{p.pinned_version}
                {p.parent_type === 'subworkflow' ? ', subworkflow' : ''})
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
