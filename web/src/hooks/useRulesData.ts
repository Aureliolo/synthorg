import { useCallback, useEffect, useRef, useState } from 'react'

import {
  listAllRules,
  type CustomRule,
  type MetricDescriptor,
  type RuleListItem,
} from '@/api/endpoints/custom-rules'
import { useCustomRulesStore } from '@/stores/custom-rules'
import { createLogger } from '@/lib/logger'
import { getErrorMessage } from '@/utils/errors'

const log = createLogger('rules-data')

let _mergedRequestToken = 0

export interface UseRulesDataReturn {
  /** All rules (built-in + custom) from /api/meta/rules. */
  allRules: readonly RuleListItem[]
  /** Custom rules from the dedicated store. */
  customRules: readonly CustomRule[]
  /** Available metric descriptors. */
  metrics: readonly MetricDescriptor[]
  /** Whether data is loading. */
  loading: boolean
  /** Error message, if any. */
  error: string | null
  /** Whether metrics are loading. */
  metricsLoading: boolean
  /** Refresh all rule data. */
  refresh: () => Promise<void>
}

export function useRulesData(): UseRulesDataReturn {
  const customRules = useCustomRulesStore((s) => s.rules)
  const metrics = useCustomRulesStore((s) => s.metrics)
  const customLoading = useCustomRulesStore((s) => s.loading)
  const customError = useCustomRulesStore((s) => s.error)
  const metricsLoading = useCustomRulesStore((s) => s.metricsLoading)
  const metricsError = useCustomRulesStore((s) => s.metricsError)

  const [allRules, setAllRules] = useState<readonly RuleListItem[]>([])
  const [mergedLoading, setMergedLoading] = useState(false)
  const [mergedError, setMergedError] = useState<string | null>(null)

  const fetchMergedRules = useCallback(async () => {
    const token = ++_mergedRequestToken
    setMergedLoading(true)
    setMergedError(null)
    try {
      const rules = await listAllRules()
      if (token !== _mergedRequestToken) return
      setAllRules(rules)
    } catch (err) {
      if (token !== _mergedRequestToken) return
      log.error('Failed to fetch merged rules', err)
      setMergedError(getErrorMessage(err))
    } finally {
      if (token === _mergedRequestToken) {
        setMergedLoading(false)
      }
    }
  }, [])

  const refresh = useCallback(async () => {
    await Promise.all([
      useCustomRulesStore.getState().fetchRules(),
      useCustomRulesStore.getState().fetchMetrics(),
      fetchMergedRules(),
    ])
  }, [fetchMergedRules])

  // Stable ref to avoid re-triggering the initial fetch effect.
  const refreshRef = useRef(refresh)
  refreshRef.current = refresh

  useEffect(() => {
    void refreshRef.current()
  }, [])

  const loading = customLoading || mergedLoading
  const error = customError ?? mergedError ?? metricsError

  return {
    allRules,
    customRules,
    metrics,
    loading,
    error,
    metricsLoading,
    refresh,
  }
}
