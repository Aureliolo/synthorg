import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { PostureBadge } from './PostureBadge'
import type { TemplateInfoResponse } from '@/api/types/setup'
import { deriveCategoryFromTags, getCategoryLabel } from '@/utils/template-categories'
import { Users, Building2, Shield, GitBranch, GitCompare, Sparkles } from 'lucide-react'

const AUTONOMY_LABELS: Record<string, string> = {
  full: 'Full autonomy',
  semi: 'Semi-autonomous',
  supervised: 'Supervised',
  locked: 'Locked',
}

const WORKFLOW_LABELS: Record<string, string> = {
  agile_kanban: 'Agile',
  kanban: 'Kanban',
  event_driven: 'Event-driven',
  waterfall: 'Waterfall',
}

function humanizeWorkflow(raw: string): string {
  return raw
    .replace(/[_-]/g, ' ')
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

function autonomyLabel(level: string | null | undefined): string {
  if (!level) return ''
  return AUTONOMY_LABELS[level] ?? level
}

/** Clean, non-monospace category pill shared across cards and the hero. */
function CategoryPill({ category }: { category: string }) {
  return (
    <span className="rounded-full border border-border bg-surface px-2 py-0.5 text-compact font-medium text-muted-foreground">
      {category}
    </span>
  )
}

function TemplateCardMetadata({ template }: { template: TemplateInfoResponse }) {
  return (
    <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs text-muted-foreground">
      <div className="flex items-center gap-1.5">
        <Users className="size-3.5 text-muted-foreground" aria-hidden="true" />
        <span>{template.agent_count} agent{template.agent_count !== 1 ? 's' : ''}</span>
      </div>
      <div className="flex items-center gap-1.5">
        <Building2 className="size-3.5 text-muted-foreground" aria-hidden="true" />
        <span>{template.department_count} dept{template.department_count !== 1 ? 's' : ''}</span>
      </div>
      <div className="flex items-center gap-1.5">
        <Shield className="size-3.5 text-muted-foreground" aria-hidden="true" />
        <span>{autonomyLabel(template.autonomy_level)}</span>
      </div>
      <div className="flex items-center gap-1.5">
        <GitBranch className="size-3.5 text-muted-foreground" aria-hidden="true" />
        <span>{WORKFLOW_LABELS[template.workflow] ?? humanizeWorkflow(template.workflow)}</span>
      </div>
    </div>
  )
}

/** Deliberate compare toggle (replaces the ambient per-card checkbox). */
function CompareButton({
  label,
  compared,
  disabled,
  onToggle,
}: {
  label: string
  compared: boolean
  disabled: boolean
  onToggle: () => void
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      onClick={onToggle}
      disabled={disabled && !compared}
      aria-pressed={compared}
      aria-label={compared ? `Remove ${label} from comparison` : `Add ${label} to comparison`}
      className={cn('shrink-0', compared && 'text-accent')}
    >
      <GitCompare className="size-3.5" aria-hidden="true" />
      {compared ? 'Added' : 'Compare'}
    </Button>
  )
}

/** Optional match ribbon shown above the title in the matches grid. */
function MatchRibbon({ best, matchPercent }: { best: boolean; matchPercent: number | null }) {
  if (!best && matchPercent == null) return null
  return (
    <div className="flex items-center justify-between gap-2">
      {best ? (
        <span className="inline-flex items-center gap-1 text-compact font-semibold uppercase tracking-wide text-accent">
          <Sparkles className="size-3" aria-hidden="true" />
          Best match
        </span>
      ) : (
        <span />
      )}
      {matchPercent != null && (
        <span className="rounded-full bg-accent/10 px-2 py-0.5 text-compact font-semibold text-accent">
          {matchPercent}% match
        </span>
      )}
    </div>
  )
}

export interface TemplateCardProps {
  template: TemplateInfoResponse
  selected: boolean
  compared: boolean
  onSelect: () => void
  onToggleCompare: () => void
  compareDisabled: boolean
  /** Match percentage shown as a pill (matches grid only). */
  matchPercent?: number | null
  /** Highlights this card as the single best match. */
  best?: boolean
}

export function TemplateCard({
  template,
  selected,
  compared,
  onSelect,
  onToggleCompare,
  compareDisabled,
  matchPercent = null,
  best = false,
}: TemplateCardProps) {
  const category = getCategoryLabel(deriveCategoryFromTags(template.tags))
  const borderClass = selected
    ? 'border-accent shadow-[var(--so-shadow-accent-glow)]'
    : best
      ? 'border-accent/50'
      : 'border-border'

  return (
    <div
      className={cn(
        'flex h-full flex-col gap-3 rounded-lg border bg-card p-card transition-colors hover:bg-card-hover',
        borderClass,
      )}
    >
      <MatchRibbon best={best} matchPercent={matchPercent} />
      {/* Click-to-select is a mouse convenience; keyboard / AT users select via
          the explicit Select button below. The body is not a role="button"
          because it nests an interactive PostureBadge (WAI-ARIA forbids it). */}
      <div onClick={onSelect} className="flex flex-1 cursor-pointer flex-col gap-2.5 text-left">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold text-foreground">{template.display_name}</h3>
          <CategoryPill category={category} />
        </div>
        <p className="line-clamp-2 text-xs text-muted-foreground">{template.description}</p>
        {template.posture != null && <PostureBadge posture={template.posture} />}
        <TemplateCardMetadata template={template} />
      </div>

      <div className="mt-auto flex items-center gap-2">
        <Button
          variant={selected ? 'default' : 'outline'}
          size="sm"
          onClick={onSelect}
          className="flex-1"
        >
          {selected ? 'Selected' : 'Select'}
        </Button>
        <CompareButton
          label={template.display_name}
          compared={compared}
          disabled={compareDisabled}
          onToggle={onToggleCompare}
        />
      </div>
    </div>
  )
}
