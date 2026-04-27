import { useCallback, useEffect, useState } from 'react'
import { Drawer } from '@/components/ui/drawer'
import { MetadataGrid } from '@/components/ui/metadata-grid'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Trash2 } from 'lucide-react'
import { listVersions, listParents } from '@/api/endpoints/subworkflows'
import { useSubworkflowsStore } from '@/stores/subworkflows'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import type { ParentReference, SubworkflowSummary } from '@/api/types/workflows'

const log = createLogger('SubworkflowDetailDrawer')

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
  const addToast = useToastStore((s) => s.add)
  const [versions, setVersions] = useState<readonly string[]>([])
  const [parents, setParents] = useState<readonly ParentReference[]>([])
  const [loading, setLoading] = useState(false)
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const detailsKey = subworkflow
    ? `${subworkflow.subworkflow_id}:${subworkflow.latest_version}`
    : null
  const [loadedKey, setLoadedKey] = useState<string | null>(null)

  useEffect(() => {
    if (!open || !subworkflow) return
    const subId = subworkflow.subworkflow_id
    const subVersion = subworkflow.latest_version
    const key = `${subId}:${subVersion}`
    let cancelled = false
    async function load() {
      setLoadedKey(null)
      setVersions([])
      setParents([])
      setLoading(true)
      try {
        const [v, p] = await Promise.all([
          listVersions(subId, { limit: 100 }),
          listParents(subId, subVersion, { limit: 100 }),
        ])
        if (!cancelled) {
          setVersions(v.data)
          setParents(p.data)
          setLoadedKey(key)
        }
      } catch (err: unknown) {
        if (!cancelled) {
          log.warn('Failed to load subworkflow details', sanitizeForLog(err))
          addToast({
            variant: 'error',
            title: 'Failed to load details',
            description: getErrorMessage(err),
          })
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
      setVersions([])
      setParents([])
      setLoadedKey(null)
      setDeleteConfirmOpen(false)
      setDeleting(false)
    }
  }, [open, subworkflow, addToast])

  const handleDelete = useCallback(async () => {
    if (!subworkflow || loading || parents.length > 0 || loadedKey !== detailsKey) return
    setDeleting(true)
    const ok = await useSubworkflowsStore
      .getState()
      .deleteSubworkflow(
        subworkflow.subworkflow_id,
        subworkflow.latest_version,
      )
    setDeleting(false)
    if (ok) {
      setDeleteConfirmOpen(false)
      onClose()
    }
  }, [subworkflow, loading, parents, loadedKey, detailsKey, onClose])

  if (!subworkflow) return null

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
                    <span className="font-medium text-foreground">
                      {p.parent_name}
                    </span>
                    <span className="ml-1 text-muted-foreground">
                      (v{p.pinned_version}{p.parent_type === 'subworkflow' ? ', subworkflow' : ''})
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="pt-2">
            <Button
              variant="destructive"
              size="sm"
              onClick={() => setDeleteConfirmOpen(true)}
              disabled={loading || parents.length > 0 || loadedKey !== detailsKey}
              title={
                loadedKey !== detailsKey
                  ? 'Details out of date -- refresh to enable delete'
                  : loading
                    ? 'Checking parent references...'
                    : parents.length > 0
                      ? 'Cannot delete: still referenced'
                      : 'Delete this subworkflow version'
              }
            >
              <Trash2 className="mr-1 size-3.5" />
              Delete Latest Version
            </Button>
          </div>
        </div>
      </Drawer>

      <ConfirmDialog
        open={deleteConfirmOpen}
        onOpenChange={(o) => {
          if (!o) setDeleteConfirmOpen(false)
        }}
        onConfirm={handleDelete}
        title="Delete Subworkflow"
        description={`Delete ${subworkflow.name} v${subworkflow.latest_version}? This cannot be undone.`}
        confirmLabel="Delete"
        variant="destructive"
        loading={deleting}
      />
    </>
  )
}
