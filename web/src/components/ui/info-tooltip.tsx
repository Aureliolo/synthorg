import type { ReactNode } from 'react'
import { Tooltip } from '@base-ui/react/tooltip'
import { cn } from '@/lib/utils'

export interface InfoTooltipProps {
  /** Tooltip body (plain text or rich nodes; non-interactive). */
  content: ReactNode
  /** The trigger element the tooltip describes (e.g. an icon). */
  children: ReactNode
  /** Optional class for the trigger wrapper span. */
  className?: string | undefined
}

/**
 * A small hover/focus tooltip for surfacing an explanation next to an
 * icon or compact control. Renders its trigger as a `<span>` (not a
 * button) so it can be nested inside an existing interactive element
 * without producing invalid nested-button markup. The popup is
 * non-interactive text/markup, animated purely via Base UI open/closed
 * data attributes (no JS timers of our own).
 */
export function InfoTooltip({ content, children, className }: InfoTooltipProps) {
  return (
    <Tooltip.Provider>
      <Tooltip.Root>
        <Tooltip.Trigger
          render={<span className={cn('inline-flex items-center', className)} />}
        >
          {children}
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Positioner sideOffset={6}>
            <Tooltip.Popup
              className={cn(
                'max-w-xs rounded-md border border-border bg-surface px-3 py-2',
                'text-compact text-foreground shadow-lg',
                'transition-[opacity,scale] duration-[var(--so-transition-default)] ease-out',
                'data-[closed]:scale-95 data-[closed]:opacity-0',
              )}
            >
              {content}
            </Tooltip.Popup>
          </Tooltip.Positioner>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  )
}
