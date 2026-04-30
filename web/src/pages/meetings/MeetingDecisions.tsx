import { CheckCircle } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import { EmptyState } from '@/components/ui/empty-state'

interface MeetingDecisionsProps {
  decisions: readonly string[]
  className?: string
}

export function MeetingDecisions({ decisions, className }: MeetingDecisionsProps) {
  if (decisions.length === 0) {
    return (
      <SectionCard title="Decisions" icon={CheckCircle} className={className}>
        <EmptyState
          icon={CheckCircle}
          title="No decisions recorded"
          description="Decisions captured during this meeting will appear here."
        />
      </SectionCard>
    )
  }

  return (
    <SectionCard title="Decisions" icon={CheckCircle} className={className}>
      <ol className="space-y-2">
        {decisions.map((decision, idx) => (
          <li key={`decision-${decision.slice(0, 40)}-${decision.length}`} className="flex items-start gap-2">
            <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-success/10 font-mono text-micro font-medium text-success">
              {idx + 1}
            </span>
            <p className="text-sm text-foreground">{decision}</p>
          </li>
        ))}
      </ol>
    </SectionCard>
  )
}
