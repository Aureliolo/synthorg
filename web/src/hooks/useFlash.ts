import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { STATUS_FLASH } from '@/lib/motion'

interface UseFlashOptions {
  /** Override flash phase duration in ms. */
  flashMs?: number
  /** Override hold phase duration in ms. */
  holdMs?: number
  /** Override fade phase duration in ms. */
  fadeMs?: number
}

interface UseFlashReturn {
  /** Whether the flash is currently active. */
  flashing: boolean
  /** CSS class name to apply during flash (empty string when not flashing). Requires a matching CSS rule for `so-flash-active` -- use `flashStyle` for built-in animation. */
  flashClassName: string
  /** Trigger a flash animation. Resets if called while already flashing. */
  triggerFlash: () => void
  /** Inline style object for the flash effect (empty object when not flashing). */
  flashStyle: React.CSSProperties
}

/**
 * Resolve the total flash duration (flash + hold + fade), falling back
 * to the design-system timing constants when the caller omits an
 * override.
 */
function _resolveTotalMs(options?: UseFlashOptions): number {
  const flashMs = options?.flashMs ?? STATUS_FLASH.flashMs
  const holdMs = options?.holdMs ?? STATUS_FLASH.holdMs
  const fadeMs = options?.fadeMs ?? STATUS_FLASH.fadeMs
  return flashMs + holdMs + fadeMs
}

/**
 * Hook for triggering a real-time update flash effect.
 *
 * Uses the STATUS_FLASH timing constants from the motion system.
 * Multiple rapid triggers reset the timer rather than stacking.
 */
export function useFlash(options?: UseFlashOptions): UseFlashReturn {
  const totalMs = _resolveTotalMs(options)
  const [flashing, setFlashing] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const triggerFlash = useCallback(() => {
    // Clear any existing timer to prevent stacking
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current)
    }
    setFlashing(true)
    timerRef.current = setTimeout(() => {
      setFlashing(false)
      timerRef.current = null
    }, totalMs)
  }, [totalMs])

  // Clear timer on unmount to prevent setState on unmounted component
  useEffect(() => {
    return () => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current)
      }
    }
  }, [])

  const flashClassName = flashing ? 'so-flash-active' : ''

  const prefersReduced = useMemo(
    () =>
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    [],
  )

  const flashStyle: React.CSSProperties =
    flashing && !prefersReduced
      ? { animation: `so-status-flash ${totalMs}ms ease-out forwards` }
      : {}

  return { flashing, flashClassName, triggerFlash, flashStyle }
}
