import type { LucideIcon } from 'lucide-react'
import { useId, type ReactNode } from 'react'
import { cn } from '@/lib/utils'

export interface SectionCardProps {
  title: string
  icon?: LucideIcon | undefined
  action?: ReactNode | undefined
  children: ReactNode
  className?: string | undefined
}

export function SectionCard({
  title,
  icon: Icon,
  action,
  children,
  className,
}: SectionCardProps) {
  const titleId = useId()

  return (
    <section
      aria-labelledby={titleId}
      className={cn(
        'rounded-lg border border-border bg-card',
        className,
      )}
    >
      <div className="flex items-center gap-2 border-b border-border p-card">
        {Icon && <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />}
        <h3 id={titleId} className="flex-1 text-sm font-semibold text-foreground">{title}</h3>
        {action}
      </div>
      <div className="p-card">
        {children}
      </div>
    </section>
  )
}
