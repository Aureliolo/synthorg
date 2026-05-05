import { cn } from '@/lib/utils'
import { Drawer } from '@/components/ui/drawer'
import { Button } from '@/components/ui/button'
import { StatPill } from '@/components/ui/stat-pill'
import type { TemplateInfoResponse } from '@/api/types/setup'
import { deriveCategoryFromTags, getCategoryLabel } from '@/utils/template-categories'

export interface TemplateCompareDrawerProps {
  open: boolean
  onClose: () => void
  templates: readonly TemplateInfoResponse[]
  onSelect: (name: string) => void
  onRemove: (name: string) => void
}

/** Tags used for estimated agent count heuristics. */
const TAG_SOLO = 'solo'
const TAG_SMALL_TEAM = 'small-team'
const TAG_ENTERPRISE = 'enterprise'
const TAG_FULL_COMPANY = 'full-company'

/** Estimated agent counts per template size category. */
const AGENT_COUNT_SOLO = 1
const AGENT_COUNT_SMALL_TEAM = 3
const AGENT_COUNT_LARGE = 12
const AGENT_COUNT_DEFAULT = 5

interface ComparisonRow {
  readonly label: string
  readonly getValue: (t: TemplateInfoResponse) => string | readonly string[]
}

/** Estimate agent count from template tags. */
function estimateAgentCount(template: TemplateInfoResponse): number {
  if (template.tags.includes(TAG_SOLO)) return AGENT_COUNT_SOLO
  if (template.tags.includes(TAG_SMALL_TEAM)) return AGENT_COUNT_SMALL_TEAM
  if (template.tags.includes(TAG_ENTERPRISE) || template.tags.includes(TAG_FULL_COMPANY)) return AGENT_COUNT_LARGE
  return AGENT_COUNT_DEFAULT
}

/** Derive category display label from template tags. */
function deriveCategory(template: TemplateInfoResponse): string {
  return getCategoryLabel(deriveCategoryFromTags(template.tags))
}

const COMPARISON_ROWS: readonly ComparisonRow[] = [
  { label: 'Category', getValue: (t) => deriveCategory(t) },
  { label: 'Estimated Agents', getValue: (t) => String(estimateAgentCount(t)) },
  { label: 'Source', getValue: (t) => t.source },
  { label: 'Tags', getValue: (t) => t.tags },
  { label: 'Skill Patterns', getValue: (t) => t.skill_patterns.map((sp) => String(sp)) },
]

/** Check whether all templates have the same value for a row. */
function valuesAreEqual(templates: readonly TemplateInfoResponse[], getValue: (t: TemplateInfoResponse) => string | readonly string[]): boolean {
  if (templates.length < 2) return true
  const first = getValue(templates[0]!)
  const firstStr = Array.isArray(first) ? first.join(',') : first
  return templates.every((t) => {
    const val = getValue(t)
    const valStr = Array.isArray(val) ? val.join(',') : val
    return valStr === firstStr
  })
}

interface ComparisonRowProps {
  row: ComparisonRow
  templates: readonly TemplateInfoResponse[]
}

function ComparisonRowEntry({ row, templates }: ComparisonRowProps) {
  const isDifferent = !valuesAreEqual(templates, row.getValue)
  return (
    <div>
      <h4 className="mb-1 text-compact uppercase tracking-wide text-muted-foreground">
        {row.label}
      </h4>
      <div className="grid gap-4" style={{ gridTemplateColumns: `repeat(${templates.length}, 1fr)` }}>
        {templates.map((t) => {
          const value = row.getValue(t)
          const display = Array.isArray(value) ? value.join(', ') : String(value)
          return (
            <div
              key={t.name}
              className={cn(
                'rounded px-2 py-1 text-xs text-foreground',
                isDifferent && 'bg-accent/5',
              )}
            >
              {row.label === 'Tags' && Array.isArray(value) && value.length > 0 ? (
                <div className="flex flex-wrap gap-1">
                  {value.map((tag: string) => (
                    <StatPill key={tag} label="" value={tag} className="text-compact" />
                  ))}
                </div>
              ) : (
                display || '--'
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function TemplateCompareDrawer({
  open,
  onClose,
  templates,
  onSelect,
  onRemove,
}: TemplateCompareDrawerProps) {
  if (templates.length < 2) return null

  return (
    <Drawer open={open} onClose={onClose} title="Compare Templates">
      <div className="space-y-4">
        {/* Column headers */}
        <div className="grid gap-4" style={{ gridTemplateColumns: `repeat(${templates.length}, 1fr)` }}>
          {templates.map((t) => (
            <div key={t.name} className="space-y-2 rounded-md border border-border p-card">
              <h3 className="text-sm font-semibold text-foreground">{t.display_name}</h3>
              <p className="text-xs text-muted-foreground line-clamp-3">{t.description}</p>
            </div>
          ))}
        </div>

        {/* Comparison rows */}
        {COMPARISON_ROWS.map((row) => (
          <ComparisonRowEntry key={row.label} row={row} templates={templates} />
        ))}

        {/* Action buttons */}
        <div className="grid gap-4 border-t border-border pt-4" style={{ gridTemplateColumns: `repeat(${templates.length}, 1fr)` }}>
          {templates.map((t) => (
            <div key={t.name} className="flex flex-col gap-2">
              <Button size="sm" onClick={() => onSelect(t.name)}>Select</Button>
              <Button variant="ghost" size="sm" onClick={() => onRemove(t.name)}>
                Remove
              </Button>
            </div>
          ))}
        </div>
      </div>
    </Drawer>
  )
}
