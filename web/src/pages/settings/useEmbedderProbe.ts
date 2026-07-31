import { useCallback, useEffect, useRef, useState } from 'react'
import { probeEmbedder } from '@/api/endpoints/memory'
import type { EmbedderProbeResponse } from '@/api/types/system'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('settings')

/** A probe outcome, together with the encoded value it was measured on. */
interface ProbeState {
  readonly forValue: string
  readonly probe: EmbedderProbeResponse | null
  readonly error: string | null
  readonly probing: boolean
  /**
   * The request this outcome is waiting on, when it is still waiting.
   *
   * Carried so "still measuring" can be read from the request itself. An
   * abort settles nothing -- neither continuation runs -- so a `probing`
   * flag alone would stay true forever, and the field would show the
   * measuring hint permanently if that value ever came back on screen.
   */
  readonly signal: AbortSignal | null
}

/** What the field should show about the current value's width. */
export interface EmbedderProbe {
  readonly probe: EmbedderProbeResponse | null
  readonly error: string | null
  readonly probing: boolean
  /** Measure `nextValue`, or clear the verdict when it cannot be probed. */
  readonly start: (
    nextValue: string,
    target: { readonly provider: string; readonly modelId: string } | null,
  ) => void
}

/** Nothing measured for the value on screen. */
const _NO_VERDICT = { probe: null, error: null, probing: false } as const

/**
 * Read back an outcome only while it still describes *value*.
 *
 * @returns The outcome for *value*, or the empty verdict.
 */
function _verdictFor(
  state: ProbeState | null,
  value: string,
): Omit<EmbedderProbe, 'start'> {
  if (state === null || state.forValue !== value) return _NO_VERDICT
  return {
    probe: state.probe,
    error: state.error,
    probing: state.probing && state.signal?.aborted !== true,
  }
}

/**
 * Measure an embedder's vector width, bound to the value it describes.
 *
 * The outcome carries the value it was measured on and is read back only
 * while that still matches, so a verdict cannot be shown against a model it
 * says nothing about. Deriving it beats clearing it in an effect: a reset
 * that runs after render shows the stale width for a frame first, and the
 * bookkeeping to suppress the reset for the selection that just started its
 * own probe is the kind that goes wrong quietly.
 *
 * @param failedHint Shown when the probe itself fails.
 * @param value The field's current encoded `MODEL_REF`.
 * @returns The verdict for `value`, and the call that starts a measurement.
 */
export function useEmbedderProbe(failedHint: string, value: string): EmbedderProbe {
  const [state, setState] = useState<ProbeState | null>(null)
  const inFlightRef = useRef<AbortController | null>(null)
  const ownedValueRef = useRef<string | null>(null)

  // Abandon a probe the operator has already moved on from. Without this,
  // changing selection twice leaves both calls running, and against a local
  // model they contend over the same cold load -- which is how three clicks
  // turned a 16-second first load into three timed-out probes.
  useEffect(() => () => inFlightRef.current?.abort(), [])

  // Release the request behind a verdict that is no longer on screen, e.g.
  // when discarding edits resets the row to its persisted value. Nothing is
  // set here: what shows is derived below, so this only stops paying for an
  // answer nobody will read. Skipped for the selection `start` just made,
  // whose own value arrives through this same prop one render later.
  useEffect(() => {
    if (ownedValueRef.current === value) return
    inFlightRef.current?.abort()
    inFlightRef.current = null
  }, [value])

  const start = useCallback(
    (
      nextValue: string,
      target: { readonly provider: string; readonly modelId: string } | null,
    ) => {
      ownedValueRef.current = nextValue
      inFlightRef.current?.abort()
      if (target === null) {
        inFlightRef.current = null
        setState({
          forValue: nextValue,
          probe: null,
          error: null,
          probing: false,
          signal: null,
        })
        return
      }
      // Measured on the operator's own selection, because the width is a
      // property of the model that only the model can answer -- and because
      // learning it after the next restart is how a perfectly good choice
      // turns out to have disabled the index.
      const controller = new AbortController()
      inFlightRef.current = controller
      setState({
        forValue: nextValue,
        probe: null,
        error: null,
        probing: true,
        signal: controller.signal,
      })
      probeEmbedder(target.provider, target.modelId, controller.signal)
        .then((result) => {
          if (controller.signal.aborted) return
          setState({
            forValue: nextValue,
            probe: result,
            error: null,
            probing: false,
            signal: null,
          })
        })
        .catch((err: unknown) => {
          // Superseded by a later selection: not a failure, and reporting one
          // would contradict the answer still on its way.
          if (controller.signal.aborted) return
          // Logged as well as shown: the hint tells this one operator their
          // probe failed, which says nothing about every probe failing after
          // a network-policy change.
          log.error('Embedder probe failed', { provider: sanitizeForLog(target.provider) }, err)
          setState({
            forValue: nextValue,
            probe: null,
            error: failedHint,
            probing: false,
            signal: null,
          })
        })
    },
    [failedHint],
  )

  return { ..._verdictFor(state, value), start }
}
