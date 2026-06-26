import { useCallback, useEffect, useRef, useState } from 'react'
import { useShallow } from 'zustand/react/shallow'

import { ACTIVE_STAGES } from '@/api/endpoints/fine-tuning'
import type {
  FineTuneDataSourceType,
  FineTuneRequest,
} from '@/api/endpoints/fine-tuning'
import { useFineTuningStore } from '@/stores/fine-tuning'

import { buildStartRequest } from './pipeline-request-builder'

export interface PipelineControlState {
  sourceDir: string
  dataSource: FineTuneDataSourceType
  showAdvanced: boolean
  epochs: string
  learningRate: string
  effectiveBatchSize: string
  loading: boolean
  isActive: boolean
  startDisabled: boolean
  showPreflightPanel: boolean
  preflight: ReturnType<typeof useFineTuningStore.getState>['preflight']
  setSourceDir: (value: string) => void
  setDataSource: (value: FineTuneDataSourceType) => void
  setShowAdvanced: (value: boolean) => void
  setEpochs: (value: string) => void
  setLearningRate: (value: string) => void
  setBatchSize: (value: string) => void
  toggleAdvanced: () => void
  handlePreflight: () => void
  handleStart: () => void
  handleCancel: () => void
}

export function usePipelineControlState(): PipelineControlState {
  const { status, preflight, loading, startRun, cancelRun, runPreflightCheck } =
    useFineTuningStore(
      useShallow((s) => ({
        status: s.status,
        preflight: s.preflight,
        loading: s.loading,
        startRun: s.startRun,
        cancelRun: s.cancelRun,
        runPreflightCheck: s.runPreflightCheck,
      })),
    )
  const [sourceDir, setSourceDir] = useState('/data/documents')
  const [dataSource, setDataSource] = useState<FineTuneDataSourceType>('directory')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [epochs, setEpochs] = useState('')
  const [learningRate, setLearningRate] = useState('')
  const batchSizeTouchedRef = useRef(false)
  const [batchSizeInput, setBatchSizeInput] = useState('')
  const setBatchSize = useCallback((value: string) => {
    batchSizeTouchedRef.current = true
    setBatchSizeInput(value)
  }, [])

  // ``buildRequest()`` keys off both ``sourceDir`` and ``dataSource``, so a
  // change to either invalidates the prior preflight; clearing it here stops a
  // stale result (and its ``startDisabled`` / recommended-batch-size state)
  // from carrying over to the new request inputs.
  useEffect(() => {
    useFineTuningStore.setState({ preflight: null })
  }, [sourceDir, dataSource])

  const effectiveBatchSize = deriveEffectiveBatchSize(
    batchSizeTouchedRef.current,
    batchSizeInput,
    preflight?.recommended_batch_size ?? null,
  )
  const isActive = status != null && ACTIVE_STAGES.has(status.stage)
  const startDisabled = loading || (preflight != null && !preflight.can_proceed)
  const showPreflightPanel = preflight != null
  const buildRequest = (): FineTuneRequest =>
    buildStartRequest({
      sourceDir,
      dataSource,
      epochs,
      learningRate,
      effectiveBatchSize,
      showAdvanced,
    })

  return {
    sourceDir,
    dataSource,
    showAdvanced,
    epochs,
    learningRate,
    effectiveBatchSize,
    loading,
    isActive,
    startDisabled,
    showPreflightPanel,
    preflight,
    setSourceDir,
    setDataSource,
    setShowAdvanced,
    setEpochs,
    setLearningRate,
    setBatchSize,
    toggleAdvanced: () => setShowAdvanced(!showAdvanced),
    handlePreflight: () => void runPreflightCheck(buildRequest()),
    handleStart: () => void startRun(buildRequest()),
    handleCancel: () => void cancelRun(),
  }
}

// When preflight arrives with a recommended batch size and the user hasn't
// typed a value yet, use the recommendation as the initial value.
function deriveEffectiveBatchSize(
  touched: boolean,
  input: string,
  recommended: number | null,
): string {
  if (!touched && input === '' && recommended != null) return String(recommended)
  return input
}
