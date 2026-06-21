import type { PostureName } from '@/api/types'
import { InfoTooltip } from '@/components/ui/info-tooltip'
import { cn } from '@/lib/utils'
import { POSTURE_INFO, type PostureInfo } from '@/utils/posture-info'

const TONE_CLASS: Record<PostureInfo['tone'], string> = {
  accent: 'border-accent/30 bg-accent/10 text-accent',
  success: 'border-success/30 bg-success/10 text-success',
  warning: 'border-warning/30 bg-warning/10 text-warning',
  danger: 'border-danger/30 bg-danger/10 text-danger',
  muted: 'border-border bg-card text-muted-foreground',
}

export interface PostureBadgeProps {
  /** The template's declared posture, or null/undefined when none. */
  posture: PostureName | null | undefined
  className?: string | undefined
}

/**
 * A compact, colour-coded badge naming a template's operating posture,
 * with a hover/focus tooltip explaining the feature-flag bundle the
 * posture activates. Renders nothing when no posture is declared.
 */
export function PostureBadge({ posture, className }: PostureBadgeProps) {
  if (posture == null) return null
  const info = POSTURE_INFO[posture]
  return (
    <InfoTooltip
      content={
        <div className="flex flex-col gap-1">
          <span className="font-semibold">{info.label}</span>
          <span>{info.description}</span>
          <span className="text-muted-foreground">{info.featureFlags.join(' · ')}</span>
        </div>
      }
    >
      <span
        // role + tabIndex make the badge a keyboard-focusable trigger so the
        // posture explanation tooltip is reachable without a pointer and AT
        // announces it as interactive; the ring gives the focus a visible state.
        role="button"
        tabIndex={0}
        className={cn(
          'inline-flex items-center rounded-sm border px-2 py-0.5',
          'text-compact font-medium',
          'focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none',
          TONE_CLASS[info.tone],
          className,
        )}
      >
        {info.label}
      </span>
    </InfoTooltip>
  )
}
