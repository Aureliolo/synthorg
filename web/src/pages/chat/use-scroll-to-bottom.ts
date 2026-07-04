import { useEffect, useRef } from 'react'

/**
 * Keep a scroll container pinned to its latest content.
 *
 * Returns a ref to attach to the scrollable element. Whenever `dep`
 * changes (typically the transcript array), the container scrolls to the
 * bottom on the next frame. Driving the scroll from an effect (rather
 * than a fire-and-forget call in a send handler) lets the cleanup cancel
 * a pending frame on unmount, so no animation-frame handle survives the
 * component.
 */
export function useScrollToBottom(dep: unknown): React.RefObject<HTMLDivElement | null> {
  const scrollRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      scrollRef.current?.scrollTo({
        top: scrollRef.current.scrollHeight,
        behavior: 'smooth',
      })
    })
    return () => cancelAnimationFrame(frame)
  }, [dep])
  return scrollRef
}
