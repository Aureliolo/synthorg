import { useCallback, useEffect, useRef, useState } from 'react'

import { createLogger } from '@/lib/logger'
import { useCustomRulesStore } from '@/stores/custom-rules'
import { getErrorMessage } from '@/utils/errors'
import type { Comparator, PreviewResult } from '@/api/endpoints/custom-rules'

const log = createLogger('rule-preview-panel')
const PREVIEW_DEBOUNCE_MS = 300

export interface RulePreviewState {
  sampleValue: string
  setSampleValue: (value: string) => void
  result: PreviewResult | null
  error: string | null
}

interface ScheduleArgs {
  sampleValue: string
  metricPath: string | null
  comparator: Comparator | null
  threshold: number
  runPreview: (value: number) => Promise<void>
  debounceRef: { current: ReturnType<typeof setTimeout> | null }
  setResult: (r: PreviewResult | null) => void
  setError: (e: string | null) => void
}

export function useRulePreview(
  metricPath: string | null,
  comparator: Comparator | null,
  threshold: number,
): RulePreviewState {
  const [sampleValue, setSampleValue] = useState('')
  const [result, setResult] = useState<PreviewResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const previewRule = useCustomRulesStore((s) => s.previewRule)

  const runPreview = useCallback(
    async (value: number) => {
      if (!metricPath || !comparator) return
      setError(null)
      try {
        const res = await previewRule({
          metric_path: metricPath,
          comparator,
          threshold,
          sample_value: value,
        })
        setResult(res)
      } catch (err) {
        log.error('Preview evaluation failed', err)
        setError(getErrorMessage(err))
        setResult(null)
      }
    },
    [metricPath, comparator, threshold, previewRule],
  )

  useEffect(
    () =>
      schedulePreview({
        sampleValue,
        metricPath,
        comparator,
        threshold,
        runPreview,
        debounceRef,
        setResult,
        setError,
      }),
    [sampleValue, metricPath, comparator, threshold, runPreview],
  )

  return { sampleValue, setSampleValue, result, error }
}

function schedulePreview(args: ScheduleArgs): () => void {
  const cleanup = () => {
    if (args.debounceRef.current) clearTimeout(args.debounceRef.current)
  }
  if (!args.metricPath || !args.comparator) return cleanup
  const parsed = parseFloat(args.sampleValue)
  cleanup()
  if (!Number.isFinite(parsed) || !Number.isFinite(args.threshold)) {
    args.debounceRef.current = setTimeout(() => {
      args.setResult(null)
      args.setError(null)
    }, 0)
    return cleanup
  }
  args.debounceRef.current = setTimeout(() => {
    void args.runPreview(parsed)
  }, PREVIEW_DEBOUNCE_MS)
  return cleanup
}
