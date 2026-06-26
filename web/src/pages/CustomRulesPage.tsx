/**
 * Custom Rules page -- list + create / edit / delete / toggle of
 * operator-authored quality rules.  Wires the existing
 * ``custom-rules`` endpoint module + Zustand store into a focused
 * page surface; the preview / validation panel reuses the store's
 * ``previewRule`` action without owning its error state.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Plus, Power } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { Pagination } from '@/components/ui/pagination'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { SearchInput } from '@/components/ui/search-input'
import { SectionCard } from '@/components/ui/section-card'
import { Skeleton } from '@/components/ui/skeleton'
import { WsConnectionBanner } from '@/components/ui/ws-connection-banner'
import { useListPagination } from '@/hooks/use-list-pagination'
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
  hasError: boolean
  rulesCount: number
  filteredCount: number
  sortedRules: readonly CustomRule[]
  onToggle: (id: string) => void
  onEdit: (rule: CustomRule) => void
  onDelete: (id: string) => void
  onCreate: () => void
}

function CustomRulesContent({
  loading,
  hasError,
  rulesCount,
  filteredCount,
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
  // When a fetch error already drives the ErrorBanner above, suppress the
  // "no rules yet" empty state so the two don't render together and imply
  // the list is genuinely empty rather than failed.
  if (rulesCount === 0 && hasError) {
    return null
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
  if (filteredCount === 0) {
    return (
      <EmptyState
        title="No rules match your search"
        description="Adjust or clear the search to see the rest of your custom rules."
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

interface CustomRulesView {
  search: string
  setSearch: (value: string) => void
  filteredCount: number
  page: number
  pageSize: number
  totalItems: number
  paginatedItems: readonly CustomRule[]
  setPage: (page: number) => void
  setPageSize: (size: number) => void
}

/** Alpha sort + name/metric search + URL-persisted client pagination. */
function useCustomRulesView(rules: readonly CustomRule[]): CustomRulesView {
  const [search, setSearch] = useState('')
  const filteredRules = useMemo(() => {
    const sorted = [...rules].sort((a, b) => a.name.localeCompare(b.name))
    const query = search.trim().toLowerCase()
    if (!query) return sorted
    return sorted.filter(
      (r) =>
        r.name.toLowerCase().includes(query)
        || r.metric_path.toLowerCase().includes(query),
    )
  }, [rules, search])

  const { page, pageSize, totalItems, paginatedItems, setPage, setPageSize, resetPage } =
    useListPagination({ items: filteredRules, namespace: 'rules' })

  const didMountRef = useRef(false)
  useEffect(() => {
    if (!didMountRef.current) {
      didMountRef.current = true
      return
    }
    resetPage()
  }, [search, resetPage])

  return {
    search,
    setSearch,
    filteredCount: filteredRules.length,
    page,
    pageSize,
    totalItems,
    paginatedItems,
    setPage,
    setPageSize,
  }
}

interface CustomRuleDialogsProps {
  createOpen: boolean
  onCreateClose: () => void
  editing: CustomRule | null
  onEditClose: () => void
  deletingId: string | null
  onDeleteClose: () => void
}

function CustomRuleDialogs({
  createOpen,
  onCreateClose,
  editing,
  onEditClose,
  deletingId,
  onDeleteClose,
}: CustomRuleDialogsProps) {
  const submitting = useCustomRulesStore((s) => s.submitting)
  const deleteRule = useCustomRulesStore((s) => s.deleteRule)
  return (
    <>
      <CustomRuleFormDrawer open={createOpen} mode="create" rule={null} onClose={onCreateClose} />
      <CustomRuleFormDrawer
        open={editing !== null}
        mode="edit"
        rule={editing}
        onClose={onEditClose}
      />
      <DeleteRuleDialog
        deletingId={deletingId}
        submitting={submitting}
        onCancel={onDeleteClose}
        onConfirm={async (id) => {
          // Only dismiss on success; on failure the store has already surfaced
          // the error toast and we keep the dialog open for an in-context retry.
          if (await deleteRule(id)) onDeleteClose()
        }}
      />
    </>
  )
}

export default function CustomRulesPage() {
  const rules = useCustomRulesStore((s) => s.rules)
  const loading = useCustomRulesStore((s) => s.loading)
  const error = useCustomRulesStore((s) => s.error)
  const fetchRules = useCustomRulesStore((s) => s.fetchRules)
  const fetchMetrics = useCustomRulesStore((s) => s.fetchMetrics)
  const toggleRule = useCustomRulesStore((s) => s.toggleRule)

  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState<CustomRule | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const view = useCustomRulesView(rules)

  useEffect(() => {
    void fetchRules()
    void fetchMetrics()
  }, [fetchRules, fetchMetrics])

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
          onRetry={() => void fetchRules()}
        />
      )}

      <WsConnectionBanner />

      {rules.length > 0 && (
        <SearchFilterSort
          search={
            <SearchInput
              value={view.search}
              onChange={view.setSearch}
              placeholder="Search rules by name or metric..."
              aria-label="Search custom rules"
            />
          }
        />
      )}

      <CustomRulesContent
        loading={loading}
        hasError={Boolean(error)}
        rulesCount={rules.length}
        filteredCount={view.filteredCount}
        sortedRules={view.paginatedItems}
        onToggle={(id) => void toggleRule(id)}
        onEdit={setEditing}
        onDelete={setDeletingId}
        onCreate={() => setCreateOpen(true)}
      />

      {view.filteredCount > 0 && (
        <Pagination
          page={view.page}
          pageSize={view.pageSize}
          total={view.totalItems}
          onPageChange={view.setPage}
          onPageSizeChange={view.setPageSize}
        />
      )}

      <CustomRuleDialogs
        createOpen={createOpen}
        onCreateClose={() => setCreateOpen(false)}
        editing={editing}
        onEditClose={() => setEditing(null)}
        deletingId={deletingId}
        onDeleteClose={() => setDeletingId(null)}
      />
    </div>
  )
}
