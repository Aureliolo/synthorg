import { useCallback, useEffect, useRef, useState } from 'react'

import { useAuth } from '@/hooks/useAuth'
import { useQualityOverridesStore } from '@/stores/quality-overrides'
import type { OverrideResponse } from '@/api/types/collaboration'

const OVERRIDE_ROLES = ['ceo', 'manager'] as const

export interface QualityOverrideController {
  override: OverrideResponse | null
  loading: boolean
  loadError: boolean
  submitting: boolean
  clearDialogOpen: boolean
  clearing: boolean
  canManageOverrides: boolean
  score: number
  reason: string
  reasonError: string | undefined
  expiresInDays: number | null
  setScore: (value: number) => void
  setReason: (value: string) => void
  setExpiresInDays: (value: number | null) => void
  setClearDialogOpen: (open: boolean) => void
  fetchOverride: () => Promise<void>
  handleSubmit: () => Promise<void>
  handleClear: () => Promise<boolean>
}

export function useQualityScoreOverride(agentId: string): QualityOverrideController {
  const fetchState = useFetchState()
  const submitState = useSubmitState()
  const formState = useFormState()
  const { userRole } = useAuth()
  const canManageOverrides =
    userRole !== null && (OVERRIDE_ROLES as readonly string[]).includes(userRole)
  const activeAgentRef = useRef(agentId)
  activeAgentRef.current = agentId
  useResetOnAgentChange(agentId, submitState, formState)

  const fetchOverride = useCallback(
    () => runFetchOverride(agentId, activeAgentRef, fetchState),
    [agentId, fetchState],
  )
  useEffect(() => {
    void fetchOverride()
  }, [fetchOverride])

  const handleSubmit = useCallback(
    () =>
      submitOverride({
        agentId,
        activeAgentRef,
        score: formState.score,
        reason: formState.reason,
        expiresInDays: formState.expiresInDays,
        setReasonError: formState.setReasonError,
        setSubmitting: submitState.setSubmitting,
        setOverride: fetchState.setOverride,
        setScore: formState.setScore,
        setReason: formState.setReason,
        setExpiresInDays: formState.setExpiresInDays,
      }),
    [agentId, fetchState, formState, submitState],
  )

  const handleClear = useCallback(
    () =>
      clearOverrideRequest({
        agentId,
        activeAgentRef,
        setClearing: submitState.setClearing,
        setOverride: fetchState.setOverride,
        setClearDialogOpen: submitState.setClearDialogOpen,
      }),
    [agentId, fetchState.setOverride, submitState],
  )

  const { override, loading, loadError } = fetchState
  const { submitting, clearDialogOpen, clearing } = submitState
  const { score, reason, reasonError, expiresInDays, setScore, setReason, setExpiresInDays } =
    formState

  return {
    override,
    loading,
    loadError,
    submitting,
    clearDialogOpen,
    clearing,
    canManageOverrides,
    score,
    reason,
    reasonError,
    expiresInDays,
    setScore,
    setReason,
    setExpiresInDays,
    setClearDialogOpen: submitState.setClearDialogOpen,
    fetchOverride,
    handleSubmit,
    handleClear,
  }
}

interface FetchState {
  override: OverrideResponse | null
  loading: boolean
  loadError: boolean
  setOverride: (v: OverrideResponse | null) => void
  setLoading: (v: boolean) => void
  setLoadError: (v: boolean) => void
}

function useFetchState(): FetchState {
  const [override, setOverride] = useState<OverrideResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)
  return { override, loading, loadError, setOverride, setLoading, setLoadError }
}

interface SubmitState {
  submitting: boolean
  clearDialogOpen: boolean
  clearing: boolean
  setSubmitting: (v: boolean) => void
  setClearDialogOpen: (v: boolean) => void
  setClearing: (v: boolean) => void
}

function useSubmitState(): SubmitState {
  const [submitting, setSubmitting] = useState(false)
  const [clearDialogOpen, setClearDialogOpen] = useState(false)
  const [clearing, setClearing] = useState(false)
  return {
    submitting,
    clearDialogOpen,
    clearing,
    setSubmitting,
    setClearDialogOpen,
    setClearing,
  }
}

interface FormState {
  score: number
  reason: string
  reasonError: string | undefined
  expiresInDays: number | null
  setScore: (v: number) => void
  setReason: (v: string) => void
  setReasonError: (v: string | undefined) => void
  setExpiresInDays: (v: number | null) => void
}

function useFormState(): FormState {
  const [score, setScore] = useState(5.0)
  const [reason, setReason] = useState('')
  const [reasonError, setReasonError] = useState<string | undefined>()
  const [expiresInDays, setExpiresInDays] = useState<number | null>(null)
  return {
    score,
    reason,
    reasonError,
    expiresInDays,
    setScore,
    setReason,
    setReasonError,
    setExpiresInDays,
  }
}

function useResetOnAgentChange(
  agentId: string,
  submitState: SubmitState,
  formState: FormState,
): void {
  // React's documented "reset state when props change" idiom: detect the prop
  // CHANGE during render so we clear transient UI flags before the new
  // agent's render commits.
  const prevAgentIdRef = useRef(agentId)
  if (prevAgentIdRef.current !== agentId) {
    prevAgentIdRef.current = agentId
    submitState.setSubmitting(false)
    submitState.setClearing(false)
    submitState.setClearDialogOpen(false)
    formState.setReasonError(undefined)
  }
}

async function runFetchOverride(
  agentId: string,
  activeAgentRef: React.MutableRefObject<string>,
  fetchState: FetchState,
): Promise<void> {
  fetchState.setLoading(true)
  fetchState.setOverride(null)
  fetchState.setLoadError(false)
  const result = await useQualityOverridesStore.getState().getOverride(agentId)
  if (activeAgentRef.current !== agentId) return
  if (result.kind === 'ok') fetchState.setOverride(result.data)
  else if (result.kind === 'error') fetchState.setLoadError(true)
  fetchState.setLoading(false)
}

interface SubmitOverrideArgs {
  agentId: string
  activeAgentRef: React.MutableRefObject<string>
  score: number
  reason: string
  expiresInDays: number | null
  setReasonError: (v: string | undefined) => void
  setSubmitting: (v: boolean) => void
  setOverride: (v: OverrideResponse | null) => void
  setScore: (v: number) => void
  setReason: (v: string) => void
  setExpiresInDays: (v: number | null) => void
}

async function submitOverride(args: SubmitOverrideArgs): Promise<void> {
  if (!args.reason.trim()) {
    args.setReasonError('Reason is required')
    return
  }
  args.setReasonError(undefined)
  args.setSubmitting(true)
  const requestAgent = args.agentId
  const data = await useQualityOverridesStore.getState().setOverride(requestAgent, {
    score: args.score,
    reason: args.reason.trim(),
    expires_in_days: args.expiresInDays,
  })
  if (args.activeAgentRef.current !== requestAgent) {
    args.setSubmitting(false)
    return
  }
  args.setSubmitting(false)
  if (data) {
    args.setOverride(data)
    args.setScore(5.0)
    args.setReason('')
    args.setExpiresInDays(null)
  }
}

interface ClearOverrideArgs {
  agentId: string
  activeAgentRef: React.MutableRefObject<string>
  setClearing: (v: boolean) => void
  setOverride: (v: OverrideResponse | null) => void
  setClearDialogOpen: (v: boolean) => void
}

async function clearOverrideRequest(args: ClearOverrideArgs): Promise<boolean> {
  args.setClearing(true)
  const requestAgent = args.agentId
  const ok = await useQualityOverridesStore.getState().clearOverride(requestAgent)
  if (args.activeAgentRef.current !== requestAgent) {
    args.setClearing(false)
    return false
  }
  args.setClearing(false)
  if (!ok) return false
  args.setOverride(null)
  args.setClearDialogOpen(false)
  return true
}
