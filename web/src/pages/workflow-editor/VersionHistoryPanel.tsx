import { Clock, GitCompare, RotateCcw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Drawer } from '@/components/ui/drawer'
import { Skeleton } from '@/components/ui/skeleton'
import { useWorkflowEditorStore } from '@/stores/workflow-editor'
import { useState } from 'react'
import { formatRelativeTime } from '@/utils/format'
import type { WorkflowDefinitionVersionSummary } from '@/api/types/workflows'

export interface VersionCardProps {
  v: WorkflowDefinitionVersionSummary
  currentVersion: number | null
  saving: boolean
  onCompare: (version: WorkflowDefinitionVersionSummary) => void
  onRestore: (version: number) => void
}

function VersionCard({ v, currentVersion, saving, onCompare, onRestore }: VersionCardProps) {
  const isCurrent = v.version === currentVersion
  return (
    <div className="flex flex-col gap-1.5 rounded-lg border border-border p-card">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="rounded-md bg-accent/10 px-1.5 py-0.5 text-xs font-medium text-accent">
            v{v.version}
          </span>
          <span className="text-sm text-foreground">{v.snapshot.name}</span>
        </div>
        {isCurrent && <span className="text-xs text-success">Current</span>}
      </div>
      <div className="flex items-center gap-2 text-xs text-muted">
        <Clock className="size-3" aria-hidden="true" />
        <time dateTime={v.saved_at}>{formatRelativeTime(v.saved_at)}</time>
        <span>by {v.saved_by}</span>
      </div>
      <div className="flex gap-1 pt-1">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onCompare(v)}
          disabled={isCurrent}
          title="Compare with current"
          aria-label={`Compare version ${v.version} with current`}
        >
          <GitCompare className="size-3.5" />
          <span className="ml-1">Compare</span>
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onRestore(v.version)}
          disabled={isCurrent || saving}
          title="Restore this version"
          aria-label={`Restore version ${v.version}`}
        >
          <RotateCcw className="size-3.5" />
          <span className="ml-1">Restore</span>
        </Button>
      </div>
    </div>
  )
}

interface VersionHistoryPanelProps {
  open: boolean
  onClose: () => void
}

export function VersionHistoryPanel({ open, onClose }: VersionHistoryPanelProps) {
  const state = useVersionHistoryState()

  return (
    <>
      <Drawer open={open} onClose={onClose} title="Version History" side="right">
        <div className="flex flex-col gap-section-gap">
          <VersionListBody state={state} />
        </div>
      </Drawer>
      <ConfirmDialog
        open={state.restoreTarget !== null}
        onOpenChange={(next) => {
          if (!next) state.setRestoreTarget(null)
        }}
        onConfirm={state.confirmRestore}
        title="Restore Version"
        description={`Restore to version ${state.restoreTarget}? This creates a new version with the old content. No history is lost.`}
        confirmLabel="Restore"
        loading={state.saving}
      />
    </>
  )
}

interface VersionHistoryState {
  versions: ReturnType<typeof useWorkflowEditorStore.getState>['versions']
  versionsLoading: boolean
  versionsHasMore: boolean
  saving: boolean
  currentVersion: number | null
  restoreTarget: number | null
  loadMoreVersions: ReturnType<typeof useWorkflowEditorStore.getState>['loadMoreVersions']
  setRestoreTarget: (value: number | null) => void
  handleCompare: (version: WorkflowDefinitionVersionSummary) => void
  confirmRestore: () => Promise<void>
}

function useVersionHistoryState(): VersionHistoryState {
  const versions = useWorkflowEditorStore((s) => s.versions)
  const versionsLoading = useWorkflowEditorStore((s) => s.versionsLoading)
  const versionsHasMore = useWorkflowEditorStore((s) => s.versionsHasMore)
  const loadDiff = useWorkflowEditorStore((s) => s.loadDiff)
  const rollback = useWorkflowEditorStore((s) => s.rollback)
  const loadMoreVersions = useWorkflowEditorStore((s) => s.loadMoreVersions)
  const saving = useWorkflowEditorStore((s) => s.saving)
  const [restoreTarget, setRestoreTarget] = useState<number | null>(null)
  const currentVersion = versions[0]?.version ?? null

  const handleCompare = (version: WorkflowDefinitionVersionSummary) => {
    if (currentVersion === null) return
    void loadDiff(version.version, currentVersion)
  }

  const confirmRestore = async () => {
    if (restoreTarget === null) return
    try {
      await rollback(restoreTarget)
    } finally {
      setRestoreTarget(null)
    }
  }

  return {
    versions,
    versionsLoading,
    versionsHasMore,
    saving,
    currentVersion,
    restoreTarget,
    loadMoreVersions,
    setRestoreTarget,
    handleCompare,
    confirmRestore,
  }
}

interface VersionListBodyProps {
  state: VersionHistoryState
}

function VersionListBody({ state }: VersionListBodyProps) {
  return (
    <>
      {state.versionsLoading && state.versions.length === 0 && (
        <div className="flex flex-col gap-grid-gap">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-16 rounded-lg" />
          ))}
        </div>
      )}
      {!state.versionsLoading && state.versions.length === 0 && (
        <p className="py-4 text-center text-sm text-muted">No version history yet</p>
      )}
      {state.versions.map((v) => (
        <VersionCard
          key={v.version}
          v={v}
          currentVersion={state.currentVersion}
          saving={state.saving}
          onCompare={state.handleCompare}
          onRestore={state.setRestoreTarget}
        />
      ))}
      {state.versionsHasMore && (
        <Button
          variant="ghost"
          size="sm"
          onClick={state.loadMoreVersions}
          disabled={state.versionsLoading}
          className="self-center"
        >
          {state.versionsLoading ? 'Loading...' : 'Load more'}
        </Button>
      )}
    </>
  )
}
