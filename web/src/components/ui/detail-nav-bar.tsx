import { useEffect } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { Button } from './button'
import { cn } from '@/lib/utils'
import { formatNumber } from '@/utils/format'

export interface DetailNavBarProps {
  /** Title or summary for the current detail (rendered next to the position counter). */
  label?: string
  /** Whether the Previous button should be active. */
  canPrev: boolean
  /** Whether the Next button should be active. */
  canNext: boolean
  /** Click / keyboard handler for Previous. */
  onPrev: () => void
  /** Click / keyboard handler for Next. */
  onNext: () => void
  /** ``null`` hides the position counter (deep link / refresh). */
  position: { current: number; total: number } | null
  className?: string
  /**
   * Bind the global keyboard shortcuts ``J`` / ``ArrowLeft`` (prev)
   * and ``K`` / ``ArrowRight`` (next). Default ``true``; pass
   * ``false`` when the host page has its own conflicting bindings.
   */
  bindShortcuts?: boolean
}

/**
 * Toolbar with Previous / position-counter / Next controls for detail
 * pages reached from a filtered list. Hidden when ``position`` is
 * ``null`` (the parent list context is not available). Keyboard
 * shortcuts: ``J`` / ``ArrowLeft`` for previous, ``K`` / ``ArrowRight``
 * for next.
 */
export function DetailNavBar({
  label,
  canPrev,
  canNext,
  onPrev,
  onNext,
  position,
  className,
  bindShortcuts = true,
}: DetailNavBarProps) {
  // Bind keyboard shortcuts at the window level. The host detail page
  // typically wraps several inputs that we don't want to hijack, so
  // we ignore key events when focus is inside an input / textarea /
  // contenteditable element.
  useEffect(() => {
    if (!bindShortcuts) return
    function isEditable(target: EventTarget | null): boolean {
      if (!(target instanceof HTMLElement)) return false
      if (target.isContentEditable) return true
      const tag = target.tagName
      return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
    }
    function onKey(event: KeyboardEvent) {
      if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return
      if (isEditable(event.target)) return
      if ((event.key === 'j' || event.key === 'ArrowLeft') && canPrev) {
        event.preventDefault()
        onPrev()
      } else if ((event.key === 'k' || event.key === 'ArrowRight') && canNext) {
        event.preventDefault()
        onNext()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('keydown', onKey) }
  }, [bindShortcuts, canPrev, canNext, onPrev, onNext])

  if (position === null) return null

  return (
    <div
      role="navigation"
      aria-label="List navigation"
      className={cn(
        'flex items-center gap-2 rounded-md border border-border bg-card/50 px-2 py-1 text-xs',
        className,
      )}
    >
      <Button
        type="button"
        size="icon-xs"
        variant="ghost"
        onClick={onPrev}
        disabled={!canPrev}
        aria-label="Previous in list (J)"
        title="Previous (J)"
      >
        <ChevronLeft className="size-4" aria-hidden="true" />
      </Button>
      <span className="font-mono text-text-secondary">
        {formatNumber(position.current)}
        <span className="px-1 text-text-muted">of</span>
        {formatNumber(position.total)}
      </span>
      {label && (
        <span className="truncate text-text-secondary">{label}</span>
      )}
      <Button
        type="button"
        size="icon-xs"
        variant="ghost"
        onClick={onNext}
        disabled={!canNext}
        aria-label="Next in list (K)"
        title="Next (K)"
      >
        <ChevronRight className="size-4" aria-hidden="true" />
      </Button>
    </div>
  )
}
