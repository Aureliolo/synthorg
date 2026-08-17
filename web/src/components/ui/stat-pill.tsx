import { cn } from '@/lib/utils'

export interface StatPillProps {
  label?: string
  value: string | number
  className?: string
}

export function StatPill({ label, value, className }: StatPillProps) {
  return (
    <span
      {...(label !== undefined && { role: 'group', 'aria-label': label })}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-sm border border-border bg-card px-2 py-0.5',
        className,
      )}
    >
      {label && (
        <span className="text-compact uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
      )}
      <span className="font-mono text-xs font-semibold text-foreground">
        {value}
      </span>
    </span>
  )
}
