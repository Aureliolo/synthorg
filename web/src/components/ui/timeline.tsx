import { cn } from '@/lib/utils'
import { statusBgClass } from '@/utils/status-color'

/** A single point on the timeline (a recorded turn). */
export interface TimelineFrame {
  /** 1-based turn index, used as the visible label and seek target. */
  readonly turnIndex: number
  /** Task status at the turn, drives the dot colour. */
  readonly status: string
}

export interface TimelineProps {
  /** Frames in ascending turn order. */
  frames: readonly TimelineFrame[]
  /** Index (into ``frames``) of the current playhead position. */
  currentIndex: number
  /** Seek to a frame by its index in ``frames``. */
  onSeek: (index: number) => void
  /** Accessible label for the scrubber. */
  label?: string
  className?: string
}

/**
 * Horizontal scrubber of recorded turns, colour-coded by status. Click a
 * dot to seek; arrow keys step, Home/End jump to the ends. Reusable for
 * any step-through replay surface, not only the flight recorder.
 */
export function Timeline({
  frames,
  currentIndex,
  onSeek,
  label = 'Replay timeline',
  className,
}: TimelineProps) {
  const lastIndex = frames.length - 1

  function onKeyDown(event: React.KeyboardEvent<HTMLDivElement>): void {
    if (frames.length === 0) return
    // Clamp ``currentIndex`` into ``[0, lastIndex]`` BEFORE deriving any
    // next target so a stale prop (e.g. after the frames array shrank
    // out from under us) cannot produce an out-of-range seek target.
    const clampedCurrent = Math.min(lastIndex, Math.max(0, currentIndex))
    if (event.key === 'ArrowRight') {
      event.preventDefault()
      onSeek(Math.min(lastIndex, clampedCurrent + 1))
    } else if (event.key === 'ArrowLeft') {
      event.preventDefault()
      onSeek(Math.max(0, clampedCurrent - 1))
    } else if (event.key === 'Home') {
      event.preventDefault()
      onSeek(0)
    } else if (event.key === 'End') {
      event.preventDefault()
      onSeek(lastIndex)
    }
  }

  return (
    <div
      role="slider"
      tabIndex={0}
      aria-label={label}
      aria-valuemin={frames.length === 0 ? undefined : 1}
      aria-valuemax={frames.length === 0 ? undefined : frames.length}
      aria-valuenow={frames.length === 0 ? undefined : currentIndex + 1}
      onKeyDown={onKeyDown}
      className={cn(
        'flex items-center gap-1 overflow-x-auto rounded-md',
        'border border-border bg-card p-card focus-visible:outline-none',
        'focus-visible:ring-2 focus-visible:ring-accent',
        className,
      )}
    >
      {frames.map((frame, index) => (
        <button
          key={frame.turnIndex}
          type="button"
          aria-label={`Turn ${String(frame.turnIndex)} (${frame.status})`}
          aria-current={index === currentIndex}
          onClick={() => onSeek(index)}
          className={cn(
            'group flex shrink-0 flex-col items-center gap-1',
            'focus-visible:outline-none',
          )}
        >
          <span
            className={cn(
              'size-3 rounded-full',
              statusBgClass(frame.status),
              index === currentIndex
                ? 'ring-2 ring-accent'
                : 'opacity-70 group-hover:opacity-100',
            )}
          />
          <span
            className={cn(
              'font-mono text-xs tabular-nums',
              index === currentIndex ? 'text-foreground' : 'text-text-secondary',
            )}
          >
            {frame.turnIndex}
          </span>
        </button>
      ))}
    </div>
  )
}
