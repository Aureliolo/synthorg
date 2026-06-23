import { TemplateCard } from './TemplateCard'
import { MAX_COMPARE, type RankedTemplate } from './template-step-data'

export interface MatchesGridProps {
  matches: readonly RankedTemplate[]
  selectedTemplate: string | null
  comparedTemplates: readonly string[]
  onSelect: (name: string) => void
  onToggleCompare: (name: string) => void
}

/**
 * Uniform grid of templates that match the stated intent, best-first. The top
 * match gets a quiet "Best match" ribbon and a match%; the rest are plain. No
 * oversized hero -- every card is the same size.
 */
export function MatchesGrid({
  matches,
  selectedTemplate,
  comparedTemplates,
  onSelect,
  onToggleCompare,
}: MatchesGridProps) {
  if (matches.length === 0) return null
  const compareDisabledFor = (name: string) =>
    comparedTemplates.length >= MAX_COMPARE && !comparedTemplates.includes(name)
  return (
    <div className="grid gap-grid-gap sm:grid-cols-2 lg:grid-cols-3">
      {matches.map((match, index) => (
        <TemplateCard
          key={match.template.name}
          template={match.template}
          selected={selectedTemplate === match.template.name}
          compared={comparedTemplates.includes(match.template.name)}
          onSelect={() => onSelect(match.template.name)}
          onToggleCompare={() => onToggleCompare(match.template.name)}
          compareDisabled={compareDisabledFor(match.template.name)}
          matchPercent={match.matchPercent}
          best={index === 0}
        />
      ))}
    </div>
  )
}
