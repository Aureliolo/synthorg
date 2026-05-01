import { useState } from 'react'
import { useShallow } from 'zustand/react/shallow'

import type { CheckpointRecord } from '@/api/endpoints/fine-tuning'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { StatusBadge } from '@/components/ui/status-badge'
import { useFineTuningStore } from '@/stores/fine-tuning'
import { formatDateOnly } from '@/utils/format'

interface CheckpointRowProps {
  checkpoint: CheckpointRecord
  onDeploy: (id: string) => void
  onRollback: (id: string) => void
  onDelete: (id: string) => void
}

function CheckpointRow({ checkpoint: cp, onDeploy, onRollback, onDelete }: CheckpointRowProps) {
  return (
    <tr>
      <td className="py-2 pr-4 font-mono text-xs">
        {formatDateOnly(cp.created_at)}
      </td>
      <td className="py-2 pr-4">{cp.base_model}</td>
      <td className="py-2 pr-4">{cp.doc_count}</td>
      <td className="py-2 pr-4">
        {cp.eval_metrics ? (
          <span>
            {(cp.eval_metrics.ndcg_at_10 * 100).toFixed(1)}%
            <MetricDelta value={cp.eval_metrics.improvement_ndcg} />
          </span>
        ) : (
          <span className="text-muted-foreground">--</span>
        )}
      </td>
      <td className="py-2 pr-4">
        {cp.eval_metrics ? (
          <span>
            {(cp.eval_metrics.recall_at_10 * 100).toFixed(1)}%
            <MetricDelta value={cp.eval_metrics.improvement_recall} />
          </span>
        ) : (
          <span className="text-muted-foreground">--</span>
        )}
      </td>
      <td className="py-2 pr-4 font-mono text-xs">
        {formatBytes(cp.size_bytes)}
      </td>
      <td className="py-2 pr-4">
        {/* StatusBadge label prop renders the default status label
            ("Active" / "Idle") inline, replacing the previous
            hand-rolled adjacent <span> wrapper. */}
        {cp.is_active ? (
          <StatusBadge status="active" label />
        ) : (
          <StatusBadge status="idle" label />
        )}
      </td>
      <td className="py-2">
        <div className="flex gap-1">
          {!cp.is_active && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => onDeploy(cp.id)}
            >
              Deploy
            </Button>
          )}
          {cp.is_active && cp.backup_config_json && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => onRollback(cp.id)}
            >
              Rollback
            </Button>
          )}
          {!cp.is_active && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onDelete(cp.id)}
            >
              Delete
            </Button>
          )}
        </div>
      </td>
    </tr>
  )
}

export function CheckpointTable() {
  const { checkpoints, deployCheckpointAction, rollbackCheckpointAction, deleteCheckpointAction } =
    useFineTuningStore(useShallow((s) => ({
      checkpoints: s.checkpoints,
      deployCheckpointAction: s.deployCheckpointAction,
      rollbackCheckpointAction: s.rollbackCheckpointAction,
      deleteCheckpointAction: s.deleteCheckpointAction,
    })))
  const [deletingId, setDeletingId] = useState<string | null>(null)

  if (checkpoints.length === 0) {
    return (
      <EmptyState
        title="No checkpoints"
        description="Run the fine-tuning pipeline to create your first checkpoint. Fine-tuned embeddings improve retrieval quality by 10-27%."
      />
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground">
            <th className="pb-2 pr-4">Date</th>
            <th className="pb-2 pr-4">Base Model</th>
            <th className="pb-2 pr-4">Docs</th>
            <th className="pb-2 pr-4">NDCG@10</th>
            <th className="pb-2 pr-4">Recall@10</th>
            <th className="pb-2 pr-4">Size</th>
            <th className="pb-2 pr-4">Status</th>
            <th className="pb-2">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {checkpoints.map((cp) => (
            <CheckpointRow
              key={cp.id}
              checkpoint={cp}
              onDeploy={(id) => void deployCheckpointAction(id)}
              onRollback={(id) => void rollbackCheckpointAction(id)}
              onDelete={setDeletingId}
            />
          ))}
        </tbody>
      </table>
      <ConfirmDialog
        open={deletingId != null}
        onOpenChange={(open) => { if (!open) setDeletingId(null) }}
        title="Delete checkpoint"
        description="This will permanently delete the fine-tuned model checkpoint and its artifacts. This action cannot be undone."
        confirmLabel="Delete"
        variant="destructive"
        onConfirm={() => {
          if (deletingId) void deleteCheckpointAction(deletingId)
          setDeletingId(null)
        }}
      />
    </div>
  )
}

function MetricDelta({ value }: { value: number }) {
  if (value === 0) return null
  const isPositive = value > 0
  return (
    <span
      className={`ml-1 text-xs font-medium ${isPositive ? 'text-success' : 'text-danger'}`}
    >
      {isPositive ? '+' : ''}
      {(value * 100).toFixed(1)}%
    </span>
  )
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
}
