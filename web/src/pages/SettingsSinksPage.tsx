import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { Activity, ArrowLeft, Plus } from 'lucide-react'
import type { SinkInfo } from '@/api/types/settings'
import type { WsEvent } from '@/api/types/websocket'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { Skeleton } from '@/components/ui/skeleton'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useSinksStore } from '@/stores/sinks'
import { SinkCard } from './settings/sinks/SinkCard'
import { SinkFormDrawer } from './settings/sinks/SinkFormDrawer'

interface SinksPage {
  sinks: SinkInfo[]
  loading: boolean
  error: string | null
  editSink: SinkInfo | null
  isNewSink: boolean
  drawerOpen: boolean
  deleteTarget: SinkInfo | null
  deleting: boolean
  testConfig: ReturnType<typeof useSinksStore.getState>['testConfig']
  setDeleteTarget: (sink: SinkInfo | null) => void
  handleEdit: (sink: SinkInfo) => void
  handleAddNew: () => void
  handleCloseDrawer: () => void
  handleSave: (sink: SinkInfo) => Promise<void>
  handleDelete: (sink: SinkInfo) => void
  handleDeleteConfirm: () => Promise<void>
}

/** Auto-refresh sinks when the observability sink settings change over WS. */
function useSinkAutoRefresh(fetchSinks: () => Promise<void> | void): void {
  const sinkHandler = useCallback(
    (event: WsEvent) => {
      const key = (event.payload as Record<string, unknown> | undefined)?.key as string | undefined
      if (key === 'observability/sink_overrides' || key === 'observability/custom_sinks') {
        void fetchSinks()
      }
    },
    [fetchSinks],
  )
  useWebSocket({ bindings: [{ channel: 'system', handler: sinkHandler }] })
}

function useSinksPage(): SinksPage {
  const sinks = useSinksStore((s) => s.sinks)
  const loading = useSinksStore((s) => s.loading)
  const error = useSinksStore((s) => s.error)
  const fetchSinks = useSinksStore((s) => s.fetchSinks)
  const saveSink = useSinksStore((s) => s.saveSink)
  const deleteSink = useSinksStore((s) => s.deleteSink)
  const testConfig = useSinksStore((s) => s.testConfig)
  const [editSinkId, setEditSinkId] = useState<string | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [isNewSink, setIsNewSink] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<SinkInfo | null>(null)
  const [deleting, setDeleting] = useState(false)
  const editSink = editSinkId ? (sinks.find((s) => s.identifier === editSinkId) ?? null) : null

  useEffect(() => {
    void fetchSinks()
  }, [fetchSinks])
  useSinkAutoRefresh(fetchSinks)

  const handleEdit = useCallback((sink: SinkInfo) => {
    setEditSinkId(sink.identifier)
    setIsNewSink(false)
    setDrawerOpen(true)
  }, [])

  const handleAddNew = useCallback(() => {
    setEditSinkId(null)
    setIsNewSink(true)
    setDrawerOpen(true)
  }, [])

  const handleCloseDrawer = useCallback(() => {
    setDrawerOpen(false)
    setEditSinkId(null)
    setIsNewSink(false)
  }, [])

  const handleSave = useCallback(
    async (sink: SinkInfo) => {
      // Sentinel contract: keep the drawer open on failure (the store
      // already toasted) so the user can retry.
      if (await saveSink(sink)) {
        setDrawerOpen(false)
        setEditSinkId(null)
        setIsNewSink(false)
      }
    },
    [saveSink],
  )

  const handleDelete = useCallback((sink: SinkInfo) => {
    setDeleteTarget(sink)
  }, [])

  const handleDeleteConfirm = useCallback(async () => {
    if (!deleteTarget) return
    setDeleting(true)
    const ok = await deleteSink(deleteTarget)
    setDeleting(false)
    if (ok) setDeleteTarget(null)
  }, [deleteSink, deleteTarget])

  return {
    sinks,
    loading,
    error,
    editSink,
    isNewSink,
    drawerOpen,
    deleteTarget,
    deleting,
    testConfig,
    setDeleteTarget,
    handleEdit,
    handleAddNew,
    handleCloseDrawer,
    handleSave,
    handleDelete,
    handleDeleteConfirm,
  }
}

interface SinksGridProps {
  sinks: SinkInfo[]
  loading: boolean
  error: string | null
  onEdit: (sink: SinkInfo) => void
  onDelete: (sink: SinkInfo) => void
}

function SinksGrid({ sinks, loading, error, onEdit, onDelete }: SinksGridProps) {
  return (
    <>
      {error && <ErrorBanner severity="error" title="Could not load sinks" description={error} />}

      {loading && sinks.length === 0 && (
        <div className="grid grid-cols-1 gap-grid-gap sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-40 rounded-lg" />
          ))}
        </div>
      )}

      {!loading && sinks.length === 0 && !error && (
        <EmptyState
          icon={Activity}
          title="No sinks configured"
          description="Log sinks will appear once the observability system is initialized."
        />
      )}

      <ErrorBoundary level="section">
        <StaggerGroup className="grid grid-cols-1 gap-grid-gap sm:grid-cols-2 lg:grid-cols-3">
          {sinks.map((sink) => (
            <StaggerItem key={sink.identifier}>
              <SinkCard sink={sink} onEdit={onEdit} onDelete={onDelete} />
            </StaggerItem>
          ))}
        </StaggerGroup>
      </ErrorBoundary>
    </>
  )
}

function deleteDialogText(target: SinkInfo | null): {
  title: string
  description: string
  confirmLabel: string
} {
  if (target?.is_default) {
    return {
      title: `Reset overrides for ${target.identifier}?`,
      description:
        'This restores the sink to its built-in defaults. The sink itself stays registered with the runtime.',
      confirmLabel: 'Reset',
    }
  }
  return {
    title: `Delete sink ${target?.identifier ?? ''}?`,
    description:
      'This removes the sink from the runtime. Logs routed to this sink will stop being written. This cannot be undone.',
    confirmLabel: 'Delete',
  }
}

function SinkDeleteDialog({
  target,
  deleting,
  onClose,
  onConfirm,
}: {
  target: SinkInfo | null
  deleting: boolean
  onClose: () => void
  onConfirm: () => void
}) {
  const text = deleteDialogText(target)
  return (
    <ConfirmDialog
      open={target !== null}
      onOpenChange={(open) => {
        if (!open && !deleting) onClose()
      }}
      title={text.title}
      description={text.description}
      confirmLabel={text.confirmLabel}
      variant="destructive"
      loading={deleting}
      onConfirm={onConfirm}
    />
  )
}

export default function SettingsSinksPage() {
  const navigate = useNavigate()
  const page = useSinksPage()

  return (
    <div className="space-y-section-gap">
      <div className="flex items-center gap-grid-gap">
        <Button variant="ghost" size="sm" onClick={() => navigate('/settings')}>
          <ArrowLeft className="mr-1.5 size-3.5" aria-hidden />
          Settings
        </Button>
        <div className="flex flex-1 items-center gap-2">
          <Activity className="size-4 text-text-secondary" aria-hidden />
          <h1 className="text-lg font-semibold text-foreground">Log Sinks</h1>
        </div>
        <Button size="sm" onClick={page.handleAddNew}>
          <Plus className="mr-1.5 size-3.5" aria-hidden />
          Add Sink
        </Button>
      </div>

      <SinksGrid
        sinks={page.sinks}
        loading={page.loading}
        error={page.error}
        onEdit={page.handleEdit}
        onDelete={page.handleDelete}
      />

      <SinkFormDrawer
        key={page.editSink?.identifier ?? (page.isNewSink ? '__new__' : '__closed__')}
        open={page.drawerOpen}
        onClose={page.handleCloseDrawer}
        sink={page.editSink}
        isNew={page.isNewSink}
        onTest={page.testConfig}
        onSave={page.handleSave}
      />

      <SinkDeleteDialog
        target={page.deleteTarget}
        deleting={page.deleting}
        onClose={() => page.setDeleteTarget(null)}
        onConfirm={page.handleDeleteConfirm}
      />
    </div>
  )
}
