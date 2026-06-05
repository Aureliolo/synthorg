/** In-flight tracker for [Add local] / [Add cloud] presses on the detected list. */

import { useRef, useState } from 'react'
import type { AddingKind } from './detected-local-utils'

export interface AddInFlightHandle {
  /** Per-preset visible state (used to disable the button after click). */
  readonly adding: Readonly<Record<string, AddingKind>>
  /**
   * Synchronous "first click wins" acquire. Returns ``true`` when the
   * caller wins the in-flight slot; ``false`` when another click is
   * already in flight for the same preset. Updates the visible
   * ``adding`` map so the button reflects intent before the next
   * render tick.
   */
  startAdd: (name: string, kind: AddingKind) => boolean
  /** Release the slot + clear the visible marker. */
  finishAdd: (name: string) => void
}

export function useAddInFlight(): AddInFlightHandle {
  // In-flight adds keyed by preset name. A map (rather than a single
  // ``{ name, kind }``) lets two concurrent adds (e.g. user clicks
  // [Add local] on Ollama while [Add cloud] is still resolving) coexist
  // without clobbering each other's in-flight markers.
  const [adding, setAdding] = useState<Record<string, AddingKind>>({})
  // Synchronous in-flight guard against rapid double-clicks. React's
  // setState is queued, so a second click within the same render tick
  // would see the same closure-captured ``adding`` and pass the
  // visible-state check. A ref-backed Set updates synchronously and
  // gives "first click wins" semantics across both add kinds.
  const inflightRef = useRef<Set<string>>(new Set())

  const startAdd = (name: string, kind: AddingKind): boolean => {
    if (inflightRef.current.has(name)) return false
    inflightRef.current.add(name)
    setAdding((prev) => ({ ...prev, [name]: kind }))
    return true
  }

  const finishAdd = (name: string): void => {
    inflightRef.current.delete(name)
    setAdding((prev) => {
      if (!(name in prev)) return prev
      const next = { ...prev }
      Reflect.deleteProperty(next, name)
      return next
    })
  }

  return { adding, startAdd, finishAdd }
}
