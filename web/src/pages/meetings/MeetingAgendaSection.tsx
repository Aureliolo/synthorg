import { ListOrdered } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import { participantName } from '@/utils/meetings'
import type { MeetingAgenda } from '@/api/types/meetings'

interface MeetingAgendaSectionProps {
  agenda: MeetingAgenda
  /** Display name per agent id, resolved by the backend for the whole meeting. */
  participantNames: Readonly<Record<string, string>>
  className?: string
}

export function MeetingAgendaSection({
  agenda,
  participantNames,
  className,
}: MeetingAgendaSectionProps) {
  return (
    <SectionCard title="Agenda" icon={ListOrdered} className={className}>
      <div className="space-y-4">
        {/* Agenda header */}
        <div className="space-y-1">
          <h3 className="text-sm font-semibold text-foreground">{agenda.title}</h3>
          {agenda.context && (
            <p className="text-sm text-text-secondary">{agenda.context}</p>
          )}
        </div>

        {/* Agenda items */}
        {agenda.items.length > 0 && (
          <ol className="space-y-3">
            {agenda.items.map((item, idx) => (
              // eslint-disable-next-line @eslint-react/no-array-index-key -- agenda items have no stable id and the ordered list is never reordered/filtered; title alone is not unique
              <li key={`agenda-${idx}`} className="flex gap-3">
                <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-accent/10 font-mono text-micro font-medium text-accent">
                  {idx + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-foreground">{item.title}</p>
                  {item.description && (
                    <p className="text-xs text-text-secondary">{item.description}</p>
                  )}
                  {item.presenter_id && (
                    <p className="mt-0.5 text-micro text-text-secondary">
                      Presenter: {participantName(participantNames, item.presenter_id)}
                    </p>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>
    </SectionCard>
  )
}
