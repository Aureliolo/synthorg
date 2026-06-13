import { FileText, Monitor, Pencil, Trash2 } from 'lucide-react'
import type { SinkInfo } from '@/api/types/settings'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'

export interface SinkCardProps {
  sink: SinkInfo
  onEdit: (sink: SinkInfo) => void
  /**
   * Optional delete handler. When absent (legacy contexts / read-only
   * previews) the Delete button is suppressed so the card still renders
   * without a wire-up.
   */
  onDelete?: ((sink: SinkInfo) => void) | undefined
}

const LEVEL_COLORS: Record<string, string> = {
  DEBUG: 'bg-text-secondary/10 text-text-secondary',
  INFO: 'bg-accent/10 text-accent',
  WARNING: 'bg-warning/10 text-warning',
  ERROR: 'bg-danger/10 text-danger',
  CRITICAL: 'bg-danger/10 text-danger',
}

function SinkCardHeader({ sink }: { sink: SinkInfo }) {
  const stateLabel = sink.enabled ? 'Enabled' : 'Disabled'
  return (
    <div className="flex items-center gap-3 p-card">
      <span className="text-text-secondary">
        {sink.sink_type === 'console' ? (
          <Monitor className="size-4" aria-hidden />
        ) : (
          <FileText className="size-4" aria-hidden />
        )}
      </span>
      <span className="min-w-0 flex-1 truncate font-mono text-xs font-medium text-foreground">
        {sink.identifier}
      </span>
      <span
        className={cn('inline-flex h-2 w-2 rounded-full', sink.enabled ? 'bg-success' : 'bg-text-muted')}
        role="status"
        aria-label={stateLabel}
        title={stateLabel}
      />
    </div>
  )
}

function SinkCardMeta({ sink }: { sink: SinkInfo }) {
  const levelColor = LEVEL_COLORS[sink.level] ?? 'bg-border text-text-muted'
  return (
    <div className="border-t border-border p-card space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className={cn('rounded px-1.5 py-0.5 text-micro font-medium uppercase', levelColor)}>
          {sink.level}
        </span>
        <span className="text-micro text-text-muted">{sink.json_format ? 'JSON' : 'Text'}</span>
        {sink.is_default && <span className="text-micro text-text-muted">Default</span>}
      </div>
      {sink.rotation && (
        <p className="text-micro text-text-muted">
          Rotation: {(sink.rotation.max_bytes / 1024 / 1024).toFixed(0)} MB x {sink.rotation.backup_count}
        </p>
      )}
      {sink.routing_prefixes.length > 0 && (
        <p className="truncate text-micro text-text-muted" title={sink.routing_prefixes.join(', ')}>
          Routes: {sink.routing_prefixes.join(', ')}
        </p>
      )}
    </div>
  )
}

function SinkCardActions({ sink, onEdit, onDelete }: SinkCardProps) {
  return (
    <div className="border-t border-border p-card flex justify-end gap-2">
      <Button variant="ghost" size="sm" onClick={() => onEdit(sink)}>
        <Pencil className="mr-1.5 size-3" aria-hidden />
        Edit
      </Button>
      {onDelete && (
        <Button
          variant="ghost"
          size="sm"
          className="text-danger hover:bg-danger/10"
          onClick={() => onDelete(sink)}
          aria-label={sink.is_default ? `Reset ${sink.identifier} overrides` : `Delete ${sink.identifier}`}
        >
          <Trash2 className="mr-1.5 size-3" aria-hidden />
          {sink.is_default ? 'Reset' : 'Delete'}
        </Button>
      )}
    </div>
  )
}

export function SinkCard({ sink, onEdit, onDelete }: SinkCardProps) {
  return (
    <div
      className={cn(
        'flex flex-col rounded-lg border border-border bg-card',
        'transition-all duration-200 hover:bg-card-hover hover:-translate-y-px hover:shadow-[var(--so-shadow-card-hover)]',
        !sink.enabled && 'opacity-50',
      )}
    >
      <SinkCardHeader sink={sink} />
      <SinkCardMeta sink={sink} />
      <SinkCardActions sink={sink} onEdit={onEdit} onDelete={onDelete} />
    </div>
  )
}
