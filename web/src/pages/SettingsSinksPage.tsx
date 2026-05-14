import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { Activity, ArrowLeft, Plus } from 'lucide-react'
import type { SinkInfo } from '@/api/types/settings'
import type { WsEvent } from '@/api/types/websocket'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { Skeleton } from '@/components/ui/skeleton'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useSinksStore } from '@/stores/sinks'
import { SinkCard } from './settings/sinks/SinkCard'
import { SinkFormDrawer } from './settings/sinks/SinkFormDrawer'

export default function SettingsSinksPage() {
  const navigate = useNavigate()
  // Per-field selectors instead of destructuring the whole store --
  // the previous form (``const {...} = useSinksStore()``) re-rendered
  // the page on every unrelated state change in the sinks store.
  // Each selector below subscribes to exactly the slice it reads.
  const sinks = useSinksStore((s) => s.sinks)
  const loading = useSinksStore((s) => s.loading)
  const error = useSinksStore((s) => s.error)
  const fetchSinks = useSinksStore((s) => s.fetchSinks)
  const saveSink = useSinksStore((s) => s.saveSink)
  const testConfig = useSinksStore((s) => s.testConfig)
  const [editSinkId, setEditSinkId] = useState<string | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [isNewSink, setIsNewSink] = useState(false)
  const editSink = editSinkId ? sinks.find((s) => s.identifier === editSinkId) ?? null : null

  useEffect(() => {
    void fetchSinks()
  }, [fetchSinks])

  // Subscribe to WS system channel for setting updates -- auto-refresh on sink config changes
  const sinkHandler = useCallback((event: WsEvent) => {
    const key = (event.payload as Record<string, unknown> | undefined)?.key as string | undefined
    if (key === 'observability/sink_overrides' || key === 'observability/custom_sinks') {
      void fetchSinks()
    }
  }, [fetchSinks])

  useWebSocket({
    bindings: [{ channel: 'system', handler: sinkHandler }],
  })

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

  const handleSave = useCallback(async (sink: SinkInfo) => {
    const ok = await saveSink(sink)
    // Per the sentinel contract, keep the drawer open on failure so the
    // user can retry; the store already emitted an error toast.
    if (ok) {
      setDrawerOpen(false)
      setEditSinkId(null)
      setIsNewSink(false)
    }
  }, [saveSink])

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
        <Button size="sm" onClick={handleAddNew}>
          <Plus className="mr-1.5 size-3.5" aria-hidden />
          Add Sink
        </Button>
      </div>

      {error && (
        <ErrorBanner severity="error" title="Could not load sinks" description={error} />
      )}

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
              <SinkCard sink={sink} onEdit={handleEdit} />
            </StaggerItem>
          ))}
        </StaggerGroup>
      </ErrorBoundary>

      <SinkFormDrawer
        key={editSink?.identifier ?? (isNewSink ? '__new__' : '__closed__')}
        open={drawerOpen}
        onClose={handleCloseDrawer}
        sink={editSink}
        isNew={isNewSink}
        onTest={testConfig}
        onSave={handleSave}
      />
    </div>
  )
}
