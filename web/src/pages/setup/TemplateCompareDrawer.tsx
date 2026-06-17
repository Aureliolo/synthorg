import { useMemo } from 'react'
import { cn } from '@/lib/utils'
import { Drawer } from '@/components/ui/drawer'
import { Button } from '@/components/ui/button'
import { StatPill } from '@/components/ui/stat-pill'
import type { TemplateInfoResponse } from '@/api/types/setup'
import { deriveCategoryFromTags, getCategoryLabel } from '@/utils/template-categories'
import { POSTURE_INFO } from '@/utils/posture-info'

export interface TemplateCompareDrawerProps {
  open: boolean
  onClose: () => void
  templates: readonly TemplateInfoResponse[]
  onSelect: (name: string) => void
  onRemove: (name: string) => void
}

interface ComparisonRow {
  readonly label: string
  readonly getValue: (t: TemplateInfoResponse) => string | readonly string[]
}

function estimateAgentCount(template: TemplateInfoResponse): number {
  const tags = template.tags
  if (tags.includes('solo')) return 1
  if (tags.includes('small-team')) return 3
  if (tags.includes('enterprise') || tags.includes('full-company')) return 12
  return 5
}

const COMPARISON_ROWS: readonly ComparisonRow[] = [
  {
    label: 'Category',
    getValue: (t) => getCategoryLabel(deriveCategoryFromTags(t.tags)),
  },
  { label: 'Estimated Agents', getValue: (t) => String(estimateAgentCount(t)) },
  {
    label: 'Posture',
    getValue: (t) => (t.posture != null ? POSTURE_INFO[t.posture].label : '--'),
  },
  { label: 'Source', getValue: (t) => t.source },
  { label: 'Tags', getValue: (t) => t.tags },
  { label: 'Skill Patterns', getValue: (t) => [...t.skill_patterns] },
]

/** Check whether all templates have the same value for a row. */
function valuesAreEqual(templates: readonly TemplateInfoResponse[], getValue: (t: TemplateInfoResponse) => string | readonly string[]): boolean {
  const first = templates[0]
  if (first === undefined) return true
  const firstValue = getValue(first)
  const firstStr = Array.isArray(firstValue) ? firstValue.join(',') : firstValue
  return templates.every((t) => {
    const val = getValue(t)
    const valStr = Array.isArray(val) ? val.join(',') : val
    return valStr === firstStr
  })
}

// The compared-template count is dynamic, so the column count travels as
// a CSS custom property consumed by the arbitrary grid-template-columns
// class below (no hardcoded layout value, no per-render inline style rule).
const COMPARE_GRID = 'grid gap-grid-gap [grid-template-columns:repeat(var(--compare-cols),minmax(0,1fr))]'

interface ComparisonRowProps {
  row: ComparisonRow
  templates: readonly TemplateInfoResponse[]
  gridVars: React.CSSProperties
}

function ComparisonRowEntry({ row, templates, gridVars }: ComparisonRowProps) {
  const isDifferent = !valuesAreEqual(templates, row.getValue)
  return (
    <div>
      <h4 className="mb-1 text-compact uppercase tracking-wide text-muted-foreground">
        {row.label}
      </h4>
      <div className={COMPARE_GRID} style={gridVars}>
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
  const gridVars = useMemo<React.CSSProperties>(
    () => ({ '--compare-cols': templates.length }) as React.CSSProperties,
    [templates.length],
  )

  if (templates.length < 2) return null

  return (
    <Drawer open={open} onClose={onClose} title="Compare Templates">
      <div className="space-y-section-gap">
        {/* Column headers */}
        <div className={COMPARE_GRID} style={gridVars}>
          {templates.map((t) => (
            <div key={t.name} className="space-y-2 rounded-md border border-border p-card">
              <h3 className="text-sm font-semibold text-foreground">{t.display_name}</h3>
              <p className="text-xs text-muted-foreground line-clamp-3">{t.description}</p>
            </div>
          ))}
        </div>

        {/* Comparison rows */}
        {COMPARISON_ROWS.map((row) => (
          <ComparisonRowEntry key={row.label} row={row} templates={templates} gridVars={gridVars} />
        ))}

        {/* Action buttons */}
        <div className={cn(COMPARE_GRID, 'border-t border-border pt-card')} style={gridVars}>
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
