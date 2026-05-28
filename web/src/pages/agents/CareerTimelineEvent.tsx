import { memo } from 'react'
import { getCareerEventColor } from '@/utils/agents'
import { formatDateTime, formatLabel } from '@/utils/format'
import { cn } from '@/lib/utils'
import type { CareerEvent } from '@/api/types/agents'

type EventColor = ReturnType<typeof getCareerEventColor>

const DOT_COLOR_CLASSES: Readonly<Record<EventColor, string>> = {
  success: 'border-success bg-success/20',
  accent: 'border-accent bg-accent/20',
  warning: 'border-warning bg-warning/20',
  danger: 'border-danger bg-danger/20',
}

const TEXT_COLOR_CLASSES: Readonly<Record<EventColor, string>> = {
  success: 'text-success',
  accent: 'text-accent',
  warning: 'text-warning',
  danger: 'text-danger',
}

interface CareerTimelineEventProps {
  event: CareerEvent
  isLast?: boolean
}

function CareerTimelineEventImpl({ event, isLast }: CareerTimelineEventProps) {
  const color = getCareerEventColor(event.event_type)
  return (
    <div className="relative flex gap-4 pb-6 last:pb-0">
      {!isLast && <div className="absolute left-1.5 top-4 bottom-0 w-px bg-border" />}
      <div
        className={cn(
          'relative z-10 mt-1 size-3.5 shrink-0 rounded-full border-2',
          DOT_COLOR_CLASSES[color],
        )}
        aria-hidden="true"
      />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span
            className={cn(
              'text-compact font-semibold uppercase tracking-wide',
              TEXT_COLOR_CLASSES[color],
            )}
          >
            {formatLabel(event.event_type)}
          </span>
          <time
            dateTime={event.timestamp}
            className="text-micro font-mono text-muted-foreground"
          >
            {formatDateTime(event.timestamp)}
          </time>
        </div>
        {event.description && (
          <p className="mt-0.5 text-sm text-secondary-foreground">{event.description}</p>
        )}
        <p className="mt-0.5 text-xs text-muted-foreground">by {event.initiated_by}</p>
      </div>
    </div>
  )
}

export const CareerTimelineEvent = memo(CareerTimelineEventImpl)
