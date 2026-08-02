import { Lock } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface ComposeSetBadgeProps {
  className?: string
}

/**
 * Marks a setting the deployment fixed when the process started. It is shown
 * here so its value is discoverable, but it cannot be edited: the process was
 * started with it and nothing short of restarting that process changes it.
 * Every setting without this badge applies without a restart.
 *
 * The `title` here is a pointer affordance only. Assistive technology gets the
 * same explanation from the notice `SettingRow` renders and references through
 * the field's `aria-describedby`, so repeating it in the badge would have it
 * announced twice for one setting.
 */
export function ComposeSetBadge({ className }: ComposeSetBadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-micro font-medium bg-warning/10 text-warning',
        className,
      )}
      title="Set by the deployment; change it where the process is launched, then restart it"
    >
      <Lock className="size-2.5" aria-hidden />
      Compose
    </span>
  )
}
