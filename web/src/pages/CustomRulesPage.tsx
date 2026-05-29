/**
 * Custom Rules page -- list + create / edit / delete / toggle of
 * operator-authored quality rules.  Wires the existing
 * ``custom-rules`` endpoint module + Zustand store into a focused
 * page surface; the preview / validation panel reuses the store's
 * ``previewRule`` action without owning its error state.
 */
import { useEffect, useMemo, useState } from 'react'
import { Plus, Power } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { SectionCard } from '@/components/ui/section-card'
import { Skeleton } from '@/components/ui/skeleton'
import { WsConnectionBanner } from '@/components/ui/ws-connection-banner'
import { useCustomRulesStore } from '@/stores/custom-rules'
import { CustomRuleFormDrawer } from './custom-rules/CustomRuleFormDrawer'
import type { CustomRule } from '@/api/endpoints/custom-rules'
import { cn } from '@/lib/utils'

const SEVERITY_CLASSES: Record<CustomRule['severity'], string> = {
  info: 'bg-info/10 text-info border-info/20',
  warning: 'bg-warning/10 text-warning border-warning/20',
  critical: 'bg-danger/10 text-danger border-danger/20',
}

function PillBadge({ label, className }: { label: string; className?: string }) {
  return (
    <span
      role="img"
      aria-label={label}
      className={cn(
        'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium',
        className,
      )}
    >
      {label}
    </span>
  )
}

function CustomRuleCard({
  rule,
  onToggle,
  onEdit,
  onDelete,
}: {
  rule: CustomRule
  onToggle: (id: string) => void
  onEdit: (rule: CustomRule) => void
  onDelete: (id: string) => void
}) {
  return (
    <SectionCard
      title={rule.name}
      action={
        <div className="flex items-center gap-grid-gap">
          <PillBadge label={rule.severity.toUpperCase()} className={SEVERITY_CLASSES[rule.severity]} />
          <PillBadge
            label={rule.enabled ? 'Active' : 'Disabled'}
            className={
              rule.enabled
                ? 'bg-success/10 text-success border-success/20'
                : 'bg-surface text-text-secondary border-border'
            }
          />
          <Button
            variant="ghost"
            size="icon"
            onClick={() => onToggle(rule.id)}
            aria-label={rule.enabled ? 'Disable rule' : 'Enable rule'}
          >
            <Power aria-hidden="true" className="size-4" />
          </Button>
          <Button variant="secondary" onClick={() => onEdit(rule)}>
            Edit
          </Button>
          <Button variant="ghost" onClick={() => onDelete(rule.id)}>
            Delete
          </Button>
        </div>
      }
    >
      <p className="mb-grid-gap text-sm text-text-secondary">{rule.description}</p>
      <dl className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-text-secondary">Metric</dt>
          <dd className="font-mono text-foreground">{rule.metric_path}</dd>
        </div>
        <div>
          <dt className="text-text-secondary">Condition</dt>
          <dd className="font-mono text-foreground">
            {rule.comparator} {rule.threshold}
          </dd>
        </div>
        <div>
          <dt className="text-text-secondary">Targets</dt>
          <dd className="text-foreground">{rule.target_altitudes.join(', ') || 'none'}</dd>
        </div>
      </dl>
    </SectionCard>
  )
}

interface CustomRulesContentProps {
  loading: boolean
  rulesCount: number
  sortedRules: readonly CustomRule[]
  onToggle: (id: string) => void
  onEdit: (rule: CustomRule) => void
  onDelete: (id: string) => void
  onCreate: () => void
}

function CustomRulesContent({
  loading,
  rulesCount,
  sortedRules,
  onToggle,
  onEdit,
  onDelete,
  onCreate,
}: CustomRulesContentProps) {
  if (loading && rulesCount === 0) {
    return (
      <div className="flex flex-col gap-grid-gap">
        {[1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-24 w-full" />
        ))}
      </div>
    )
  }
  if (rulesCount === 0) {
    return (
      <EmptyState
        title="No custom rules yet"
        description="Custom rules let you trigger improvement proposals when an observed metric crosses a threshold."
        action={{ label: 'Create your first rule', onClick: onCreate }}
      />
    )
  }
  return (
    <ul className="flex flex-col gap-grid-gap">
      {sortedRules.map((rule) => (
        <li key={rule.id}>
          <CustomRuleCard rule={rule} onToggle={onToggle} onEdit={onEdit} onDelete={onDelete} />
        </li>
      ))}
    </ul>
  )
}

function DeleteRuleDialog({
  deletingId,
  submitting,
  onCancel,
  onConfirm,
}: {
  deletingId: string | null
  submitting: boolean
  onCancel: () => void
  onConfirm: (id: string) => Promise<void>
}) {
  return (
    <ConfirmDialog
      open={deletingId !== null}
      onOpenChange={(next) => {
        if (!next) onCancel()
      }}
      title="Delete custom rule"
      description="The rule will stop firing immediately. This cannot be undone."
      variant="destructive"
      confirmLabel={submitting ? 'Deleting…' : 'Delete'}
      loading={submitting}
      onConfirm={async () => {
        if (deletingId !== null) await onConfirm(deletingId)
      }}
    />
  )
}

export default function CustomRulesPage() {
  const rules = useCustomRulesStore((s) => s.rules)
  const loading = useCustomRulesStore((s) => s.loading)
  const error = useCustomRulesStore((s) => s.error)
  const submitting = useCustomRulesStore((s) => s.submitting)
  const fetchRules = useCustomRulesStore((s) => s.fetchRules)
  const fetchMetrics = useCustomRulesStore((s) => s.fetchMetrics)
  const deleteRule = useCustomRulesStore((s) => s.deleteRule)
  const toggleRule = useCustomRulesStore((s) => s.toggleRule)

  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState<CustomRule | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  useEffect(() => {
    void fetchRules()
    void fetchMetrics()
  }, [fetchRules, fetchMetrics])

  const sortedRules = useMemo(
    () => [...rules].sort((a, b) => a.name.localeCompare(b.name)),
    [rules],
  )

  return (
    <div className="flex flex-col gap-section-gap">
      <ListHeader
        title="Custom rules"
        count={rules.length}
        primaryAction={
          <Button onClick={() => setCreateOpen(true)}>
            <Plus aria-hidden="true" className="size-4" />
            New rule
          </Button>
        }
      />

      {error && (
        <ErrorBanner
          severity="error"
          title="Could not load custom rules"
          description={error}
          onRetry={() => {
            void fetchRules()
          }}
        />
      )}

      <WsConnectionBanner />

      <CustomRulesContent
        loading={loading}
        rulesCount={rules.length}
        sortedRules={sortedRules}
        onToggle={(id) => {
          void toggleRule(id)
        }}
        onEdit={setEditing}
        onDelete={setDeletingId}
        onCreate={() => setCreateOpen(true)}
      />

      <CustomRuleFormDrawer
        open={createOpen}
        mode="create"
        rule={null}
        onClose={() => setCreateOpen(false)}
      />

      <CustomRuleFormDrawer
        open={editing !== null}
        mode="edit"
        rule={editing}
        onClose={() => setEditing(null)}
      />

      <DeleteRuleDialog
        deletingId={deletingId}
        submitting={submitting}
        onCancel={() => setDeletingId(null)}
        onConfirm={async (id) => {
          const ok = await deleteRule(id)
          // Only dismiss on success; on failure the store has
          // already surfaced the error toast and we keep the
          // dialog open so the user can retry in context.
          if (ok) setDeletingId(null)
        }}
      />
    </div>
  )
}
