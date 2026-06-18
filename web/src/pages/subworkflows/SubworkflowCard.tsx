import { memo } from 'react'
import { Layers, GitBranch, ArrowRightLeft } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import type { SubworkflowSummary } from '@/api/types/workflows'

interface SubworkflowCardProps {
  subworkflow: SubworkflowSummary
  onClick: (subworkflow: SubworkflowSummary) => void
}

function SubworkflowCardInner({ subworkflow, onClick }: SubworkflowCardProps) {
  const versionBadge = (
    <span className="shrink-0 rounded-md bg-accent/10 px-1.5 py-0.5 text-xs font-medium text-accent">
      v{subworkflow.latest_version}
    </span>
  )

  return (
    <button
      type="button"
      className="w-full text-left rounded-lg transition-all duration-200 hover:-translate-y-px hover:shadow-[var(--so-shadow-card-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      onClick={() => onClick(subworkflow)}
      aria-label={`Subworkflow: ${subworkflow.name}`}
    >
      <SectionCard title={subworkflow.name} icon={Layers} action={versionBadge} className="h-full">
        <div className="flex flex-col gap-2">
          {subworkflow.description && (
            <p className="line-clamp-2 text-xs text-muted-foreground">
              {subworkflow.description}
            </p>
          )}

          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <span className="flex items-center gap-1" title="Inputs">
              <ArrowRightLeft className="size-3" aria-hidden="true" />
              {subworkflow.input_count}in / {subworkflow.output_count}out
            </span>
            <span className="flex items-center gap-1" title="Versions">
              <GitBranch className="size-3" aria-hidden="true" />
              {subworkflow.version_count} version{subworkflow.version_count !== 1 ? 's' : ''}
            </span>
          </div>
        </div>
      </SectionCard>
    </button>
  )
}

export const SubworkflowCard = memo(SubworkflowCardInner)
