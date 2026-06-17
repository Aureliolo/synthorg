import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { StatPill } from '@/components/ui/stat-pill'
import { PostureBadge } from './PostureBadge'
import type { TemplateInfoResponse } from '@/api/types/setup'
import { deriveCategoryFromTags, getCategoryLabel } from '@/utils/template-categories'
import { Users, Building2, Shield, GitBranch } from 'lucide-react'

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
      <div className="flex items-center gap-1.5" title="Agents">
        <Users className="size-3.5 text-accent" aria-hidden="true" />
        <span>{template.agent_count} agent{template.agent_count !== 1 ? 's' : ''}</span>
      </div>
      <div className="flex items-center gap-1.5" title="Departments">
        <Building2 className="size-3.5 text-accent" aria-hidden="true" />
        <span>{template.department_count} dept{template.department_count !== 1 ? 's' : ''}</span>
      </div>
      <div className="flex items-center gap-1.5" title="Autonomy level">
        <Shield className="size-3.5 text-accent" aria-hidden="true" />
        <span>{autonomyLabel(template.autonomy_level)}</span>
      </div>
      <div className="flex items-center gap-1.5" title="Workflow">
        <GitBranch className="size-3.5 text-accent" aria-hidden="true" />
        <span>{WORKFLOW_LABELS[template.workflow] ?? humanizeWorkflow(template.workflow)}</span>
      </div>
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
        'flex flex-col gap-3 rounded-lg border bg-card p-card transition-colors',
        selected ? 'border-accent shadow-[var(--so-shadow-accent-glow)]' : 'border-border',
        'hover:bg-card-hover',
      )}
    >
      {/* Compare checkbox */}
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

      {/* Name + category + recommended */}
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

      {/* Structural metadata */}
      <TemplateCardMetadata template={template} />

      {/* Tags */}
      <div className="flex flex-wrap gap-1">
        {template.tags.map((tag) => (
          <StatPill key={tag} value={tag} className="text-compact" />
        ))}
      </div>

      {/* Select button */}
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
