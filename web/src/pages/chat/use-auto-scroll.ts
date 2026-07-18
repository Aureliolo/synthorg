import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Keep a scroll container pinned to its latest content, but yield to the
 * operator the moment they scroll up to read earlier turns.
 *
 * Unlike a naive scroll-to-bottom, this pauses auto-follow when the user
 * scrolls away from the bottom (so reading back is never yanked down by a
 * streamed token or a new turn) and surfaces a "jump to latest" affordance
 * instead. Re-following resumes automatically once they scroll back to the
 * bottom. Driving the scroll from effects lets cleanup cancel any pending
 * animation frame, so no handle survives the component.
 */

// Treat "within this many px of the bottom" as pinned, so sub-pixel rounding
// and a slightly-short final line never wrongly read as "scrolled up".
const BOTTOM_THRESHOLD_PX = 48

export interface AutoScroll {
  /** Attach to the scrollable transcript element. */
  scrollRef: React.RefObject<HTMLDivElement | null>
  /** True when the operator has scrolled up, so a jump pill should show. */
  showJumpToLatest: boolean
  /** Scroll to the newest turn and resume auto-follow. */
  jumpToLatest: () => void
}

function isPinnedToBottom(el: HTMLDivElement): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_THRESHOLD_PX
}

export function useAutoScroll(dep: unknown): AutoScroll {
  const scrollRef = useRef<HTMLDivElement>(null)
  // Auto-follow is on until the operator scrolls up; a ref (not state) so the
  // scroll listener reads the live value without re-subscribing every render.
  const followRef = useRef(true)
  const [showJumpToLatest, setShowJumpToLatest] = useState(false)

  const scrollToBottom = useCallback((behavior: ScrollBehavior) => {
    const el = scrollRef.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior })
  }, [])

  const jumpToLatest = useCallback(() => {
    followRef.current = true
    setShowJumpToLatest(false)
    scrollToBottom('smooth')
  }, [scrollToBottom])

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const onScroll = () => {
      const pinned = isPinnedToBottom(el)
      followRef.current = pinned
      setShowJumpToLatest(!pinned)
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => {
    if (!followRef.current) return
    // Immediate ('auto') follow while content grows: 'smooth' animation emits
    // intermediate scroll events where the viewport is briefly unpinned, which
    // would flip followRef off and stall auto-follow mid-stream. Smooth scroll
    // stays reserved for the explicit jump-to-latest affordance.
    const frame = requestAnimationFrame(() => scrollToBottom('auto'))
    return () => cancelAnimationFrame(frame)
  }, [dep, scrollToBottom])

  return { scrollRef, showJumpToLatest, jumpToLatest }
}
