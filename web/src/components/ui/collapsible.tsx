import { useCallback, useId, useState, type ReactNode } from 'react'
import { ChevronDown } from 'lucide-react'
import { cn, FOCUS_RING } from '@/lib/utils'

export interface CollapsibleProps {
  /** Title rendered in the trigger row. */
  title: ReactNode
  /** Optional right-aligned summary (count, status, badge). */
  summary?: ReactNode
  /** Initial open state. Uncontrolled by default; passes through `controlled` if both are provided. */
  defaultOpen?: boolean
  /** Controlled open state. When set, `onOpenChange` must also be provided. */
  open?: boolean
  /** Notified when the user toggles the section. */
  onOpenChange?: (open: boolean) => void
  /** Body content; rendered when expanded. */
  children: ReactNode
  /** Optional className applied to the outer wrapper. */
  className?: string
  /** Optional className applied to the body when expanded. */
  contentClassName?: string
}

/**
 * Disclosure / collapsible section primitive.
 *
 * Renders an animated chevron-trigger row and lazily reveals the body
 * on click. Accessible by default: the trigger is a `<button>` with
 * `aria-expanded` and `aria-controls` pointing at the body.
 *
 * Use this for grouping per-section content on long pages (Reports,
 * Settings, MCP catalog) so users can collapse what they don't need
 * without losing context.
 */
export function Collapsible({
  title,
  summary,
  defaultOpen = true,
  open: controlledOpen,
  onOpenChange,
  children,
  className,
  contentClassName,
}: CollapsibleProps) {
  const generatedId = useId()
  const isControlled = controlledOpen !== undefined
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen)
  const open = isControlled ? controlledOpen : uncontrolledOpen

  const toggle = useCallback(() => {
    const next = !open
    if (!isControlled) setUncontrolledOpen(next)
    onOpenChange?.(next)
  }, [isControlled, onOpenChange, open])

  const bodyId = `collapsible-body-${generatedId}`

  return (
    <section className={cn('rounded-lg border border-border bg-card', className)}>
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        aria-controls={bodyId}
        className={cn(
          'flex w-full items-center justify-between gap-3 p-card text-left transition-colors hover:bg-card-hover rounded-lg',
          FOCUS_RING,
        )}
      >
        <span className="flex flex-1 items-center gap-2 text-sm font-medium text-foreground">
          <ChevronDown
            aria-hidden="true"
            className={cn(
              'size-4 shrink-0 transition-transform duration-[var(--so-transition-medium)]',
              open ? 'rotate-0' : '-rotate-90',
            )}
          />
          {title}
        </span>
        {summary !== undefined && summary !== null && summary !== false && (
          <span className="text-xs text-muted-foreground">{summary}</span>
        )}
      </button>
      {open && (
        <div id={bodyId} className={cn('border-t border-border p-card', contentClassName)}>
          {children}
        </div>
      )}
    </section>
  )
}
