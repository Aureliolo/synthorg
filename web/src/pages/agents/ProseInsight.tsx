import { Lightbulb } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ProseInsightProps {
  insights: string[]
  className?: string
}

export function ProseInsight({ insights, className }: ProseInsightProps) {
  if (insights.length === 0) return null

  return (
    <div
      className={cn(
        'flex gap-3 rounded-lg border-l-2 border-accent/40 bg-card p-card',
        className,
      )}
    >
      <Lightbulb className="size-4 shrink-0 text-accent mt-0.5" aria-hidden="true" />
      <div className="space-y-1">
        {insights.map((insight, index) => (
          // eslint-disable-next-line @eslint-react/no-array-index-key -- insight strings may duplicate; index as tiebreaker
          <p key={`${insight}-${index}`} className="text-sm italic text-secondary-foreground">
            {insight}
          </p>
        ))}
      </div>
    </div>
  )
}
