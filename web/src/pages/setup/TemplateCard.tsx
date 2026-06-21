import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { StatPill } from '@/components/ui/stat-pill'
import { PostureBadge } from './PostureBadge'
import type { TemplateInfoResponse } from '@/api/types/setup'
import { deriveCategoryFromTags, getCategoryLabel } from '@/utils/template-categories'
import { Users, Building2, Shield, GitBranch } from 'lucide-react'

/** Tags rendered before collapsing the remainder into a "+N more" pill. */
const MAX_VISIBLE_TAGS = 4

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

function TemplateCardMetadata({ template }: { template: TemplateInfoResponse }) {
  return (
    <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-muted-foreground">
      <div className="flex items-center gap-1.5">
        <Users className="size-3.5 text-accent" aria-hidden="true" />
        <span>{template.agent_count} agent{template.agent_count !== 1 ? 's' : ''}</span>
      </div>
      <div className="flex items-center gap-1.5">
        <Building2 className="size-3.5 text-accent" aria-hidden="true" />
        <span>{template.department_count} dept{template.department_count !== 1 ? 's' : ''}</span>
      </div>
      <div className="flex items-center gap-1.5">
        <Shield className="size-3.5 text-accent" aria-hidden="true" />
        <span>{autonomyLabel(template.autonomy_level)}</span>
      </div>
      <div className="flex items-center gap-1.5">
        <GitBranch className="size-3.5 text-accent" aria-hidden="true" />
        <span>{WORKFLOW_LABELS[template.workflow] ?? humanizeWorkflow(template.workflow)}</span>
      </div>
    </div>
  )
}

function TemplateCardTags({ tags }: { tags: readonly string[] }) {
  const visible = tags.slice(0, MAX_VISIBLE_TAGS)
  const overflow = tags.length - visible.length
  return (
    <div className="flex flex-wrap gap-1">
      {visible.map((tag) => (
        <StatPill key={tag} value={tag} className="text-compact" />
      ))}
      {overflow > 0 && <StatPill value={`+${overflow} more`} className="text-compact" />}
    </div>
  )
}

function TemplateCardBody({
  template,
  category,
  recommended,
  onSelect,
}: {
  template: TemplateInfoResponse
  category: string
  recommended?: boolean | undefined
  onSelect: () => void
}) {
  return (
    // Click-to-select is a mouse convenience only; keyboard + assistive-tech
    // users select via the explicit <Button> below. Making this body a
    // role="button" would nest interactive controls (PostureBadge is itself
    // role="button"), which violates WAI-ARIA and breaks focus/announcement.
    <div
      onClick={onSelect}
      className="flex flex-1 cursor-pointer flex-col gap-grid-gap rounded-md text-left"
    >
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-foreground">{template.display_name}</h3>
          {recommended && (
            <span className="inline-flex items-center rounded-full bg-accent/10 px-2 py-0.5 text-compact font-medium text-accent">
              Recommended
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <StatPill value={category} className="text-compact" />
        </div>
        <p className="line-clamp-2 text-xs text-muted-foreground">{template.description}</p>
        {template.posture != null && (
          <div className="flex items-center gap-1.5 pt-0.5">
            <PostureBadge posture={template.posture} />
          </div>
        )}
      </div>

      <TemplateCardMetadata template={template} />
      <TemplateCardTags tags={template.tags} />
    </div>
  )
}

export interface TemplateCardProps {
  template: TemplateInfoResponse
  selected: boolean
  compared: boolean
  recommended?: boolean
  onSelect: () => void
  onToggleCompare: () => void
  compareDisabled: boolean
}

export function TemplateCard({
  template,
  selected,
  compared,
  recommended,
  onSelect,
  onToggleCompare,
  compareDisabled,
}: TemplateCardProps) {
  const category = getCategoryLabel(deriveCategoryFromTags(template.tags))

  return (
    <div
      className={cn(
        // min-height keeps cards even across a grid row regardless of
        // description / tag length.
        'flex min-h-64 flex-col gap-grid-gap rounded-lg border bg-card p-card transition-colors',
        selected ? 'border-accent shadow-[var(--so-shadow-accent-glow)]' : 'border-border',
        'hover:bg-card-hover',
      )}
    >
      {/* Compare checkbox (kept outside the selectable body) */}
      <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer">
        <input
          type="checkbox"
          checked={compared}
          onChange={onToggleCompare}
          disabled={compareDisabled && !compared}
          className="accent-accent"
          aria-label={`Compare ${template.display_name}`}
        />
        Compare
      </label>

      {/* Selectable body: the whole card region selects the template, not
          just the button below it. */}
      <TemplateCardBody
        template={template}
        category={category}
        recommended={recommended}
        onSelect={onSelect}
      />

      {/* Explicit select affordance */}
      <Button
        variant={selected ? 'default' : 'outline'}
        size="sm"
        onClick={onSelect}
        className="mt-auto"
      >
        {selected ? 'Selected' : 'Select'}
      </Button>
    </div>
  )
}
