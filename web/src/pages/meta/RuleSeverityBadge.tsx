import { cn } from '@/lib/utils'

const SEVERITY_STYLES = {
  critical: 'bg-danger/15 text-danger',
  warning: 'bg-warning/15 text-warning',
  info: 'bg-accent/15 text-accent',
} as const

const SEVERITY_LABELS = {
  critical: 'Critical',
  warning: 'Warning',
  info: 'Info',
} as const

type Severity = keyof typeof SEVERITY_STYLES

function isSeverity(value: string): value is Severity {
  return Object.hasOwn(SEVERITY_STYLES, value)
}

interface RuleSeverityBadgeProps {
  severity: string
  className?: string
}

export function RuleSeverityBadge({ severity, className }: RuleSeverityBadgeProps) {
  const key: Severity = isSeverity(severity) ? severity : 'info'
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-compact font-medium',
        SEVERITY_STYLES[key],
        className,
      )}
    >
      {SEVERITY_LABELS[key]}
    </span>
  )
}
