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

      {loading && rules.length === 0 ? (
        <div className="flex flex-col gap-grid-gap">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
      ) : rules.length === 0 ? (
        <EmptyState
          title="No custom rules yet"
          description="Custom rules let you trigger improvement proposals when an observed metric crosses a threshold."
          action={{
            label: 'Create your first rule',
            onClick: () => setCreateOpen(true),
          }}
        />
      ) : (
        <ul className="flex flex-col gap-grid-gap">
          {sortedRules.map((rule) => (
            <li key={rule.id}>
              <SectionCard
                title={rule.name}
                action={
                  <div className="flex items-center gap-grid-gap">
                    <PillBadge
                      label={rule.severity.toUpperCase()}
                      className={SEVERITY_CLASSES[rule.severity]}
                    />
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
                      onClick={() => {
                        void toggleRule(rule.id)
                      }}
                      aria-label={
                        rule.enabled ? 'Disable rule' : 'Enable rule'
                      }
                    >
                      <Power aria-hidden="true" className="size-4" />
                    </Button>
                    <Button
                      variant="secondary"
                      onClick={() => setEditing(rule)}
                    >
                      Edit
                    </Button>
                    <Button
                      variant="ghost"
                      onClick={() => setDeletingId(rule.id)}
                    >
                      Delete
                    </Button>
                  </div>
                }
              >
                <p className="mb-grid-gap text-sm text-text-secondary">
                  {rule.description}
                </p>
                <dl className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
                  <div>
                    <dt className="text-text-secondary">Metric</dt>
                    <dd className="font-mono text-foreground">
                      {rule.metric_path}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-text-secondary">Condition</dt>
                    <dd className="font-mono text-foreground">
                      {rule.comparator} {rule.threshold}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-text-secondary">Targets</dt>
                    <dd className="text-foreground">
                      {rule.target_altitudes.join(', ') || 'none'}
                    </dd>
                  </div>
                </dl>
              </SectionCard>
            </li>
          ))}
        </ul>
      )}

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

      <ConfirmDialog
        open={deletingId !== null}
        onOpenChange={(next) => {
          if (!next) setDeletingId(null)
        }}
        title="Delete custom rule"
        description="The rule will stop firing immediately. This cannot be undone."
        variant="destructive"
        confirmLabel={submitting ? 'Deleting…' : 'Delete'}
        loading={submitting}
        onConfirm={async () => {
          if (deletingId === null) {
            setDeletingId(null)
            return
          }
          const ok = await deleteRule(deletingId)
          // Only dismiss on success; on failure the store has
          // already surfaced the error toast and we keep the
          // dialog open so the user can retry in context.
          if (ok) setDeletingId(null)
        }}
      />
    </div>
  )
}
