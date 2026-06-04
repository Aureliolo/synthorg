import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import { useBlocker } from 'react-router'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('unsaved-changes-guard')

export interface UseUnsavedChangesGuardOptions {
  /** Whether the form is dirty (has unsaved changes). */
  when: boolean
  /** Message shown in the ConfirmDialog. Default: "Discard unsaved changes?". */
  message?: string
  /** localStorage key for auto-save. Omit to disable draft persistence. */
  draftKey?: string
  /** Serializer invoked on every change (debounced) to produce the draft payload. */
  draftData?: () => unknown
  /**
   * Signal that the draft payload has changed so the debounced write effect
   * reschedules. Callers typically pass a JSON-serialized snapshot, a version
   * counter bumped on every edit, or the dirty form value itself. Omit to
   * schedule only once when `when` flips to true.
   */
  draftTrigger?: unknown
  /** Debounce interval for draft writes. Default 500ms. */
  draftDebounceMs?: number
  /** Callback when the user confirms "discard changes". Called after navigation proceeds. */
  onDiscard?: () => void
}

export interface UseUnsavedChangesGuardResult<T = unknown> {
  /** True when a confirmation dialog should be shown (navigation is pending). */
  confirmOpen: boolean
  /** Confirm discard: allows the pending navigation to proceed. */
  proceed: () => void
  /** Cancel the discard: keeps the user on the current page. */
  cancel: () => void
  /** Configured discard message (pass to ConfirmDialog as description). */
  message: string
  /** True when a draft exists in localStorage and has not been loaded/discarded yet. */
  hasDraft: boolean
  /** Read the persisted draft payload. Returns null if no draft. */
  restoreDraft: () => T | null
  /** Delete the persisted draft. Call after a successful save. */
  discardDraft: () => void
}

const DEFAULT_MESSAGE = 'Discard unsaved changes?'

function readDraft<T>(key: string | undefined): T | null {
  if (!key || typeof window === 'undefined') return null
  try {
    const raw = window.localStorage.getItem(key)
    if (!raw) return null
    return JSON.parse(raw) as T
  } catch (err) {
    log.warn('failed to read draft', { key: sanitizeForLog(key) }, err)
    return null
  }
}

function writeDraft(key: string, data: unknown): boolean {
  if (typeof window === 'undefined') return false
  try {
    window.localStorage.setItem(key, JSON.stringify(data))
    return true
  } catch (err) {
    log.warn('failed to persist draft', { key: sanitizeForLog(key) }, err)
    return false
  }
}

function removeDraft(key: string): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.removeItem(key)
  } catch (err) {
    log.warn('failed to remove draft', { key: sanitizeForLog(key) }, err)
  }
}

function _hasStoredDraft(key: string | undefined): boolean {
  if (!key || typeof window === 'undefined') return false
  try {
    return window.localStorage.getItem(key) !== null
  } catch (err) {
    log.warn('failed to read draft on mount', { key: sanitizeForLog(key) }, err)
    return false
  }
}

interface UseBeforeUnloadGuardArgs {
  readonly when: boolean
}

/**
 * Wire `window.beforeunload` so the browser shows its native discard
 * dialog when the user closes / reloads the tab with unsaved changes.
 */
function useBeforeUnloadGuard({ when }: UseBeforeUnloadGuardArgs): void {
  useEffect(() => {
    if (!when) return
    const handler = (event: BeforeUnloadEvent): void => {
      event.preventDefault()
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [when])
}

interface UseDraftPersistenceArgs {
  readonly when: boolean
  readonly draftKey?: string
  readonly draftData?: () => unknown
  readonly draftTrigger?: unknown
  readonly draftDebounceMs: number
}

interface DraftPersistence<T> {
  readonly hasDraft: boolean
  readonly setHasDraft: (v: boolean) => void
  readonly draftTimerRef: RefObject<ReturnType<typeof setTimeout> | null>
  readonly restoreDraft: () => T | null
  readonly discardDraft: () => void
}

/**
 * Sub-hook owning localStorage draft persistence: initial mount state,
 * debounced writes on every edit, and a `discardDraft` action that
 * cancels any pending write before clearing storage.
 */
function useDraftPersistence<T>(args: UseDraftPersistenceArgs): DraftPersistence<T> {
  const { when, draftKey, draftData, draftTrigger, draftDebounceMs } = args
  const [hasDraft, setHasDraft] = useState<boolean>(() => _hasStoredDraft(draftKey))
  const draftTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const draftDataRef = useRef(draftData)
  draftDataRef.current = draftData

  // Refresh hasDraft whenever draftKey changes so callers see the correct
  // state after navigating between forms that share the hook.
  useEffect(() => {
    // eslint-disable-next-line @eslint-react/set-state-in-effect -- draftKey-driven reconciliation
    setHasDraft(_hasStoredDraft(draftKey))
  }, [draftKey])

  // Debounced draft persistence. `draftTrigger` is any serialisable marker
  // derived from the form payload that changes on every edit; we reschedule
  // the debounce whenever it changes so subsequent edits land in storage too.
  useEffect(() => {
    if (!draftKey || !draftDataRef.current || !when) return
    if (draftTimerRef.current) clearTimeout(draftTimerRef.current)
    draftTimerRef.current = setTimeout(() => {
      const serializer = draftDataRef.current
      if (!serializer) return
      // Only flip hasDraft to true on a successful write: a quota/privacy
      // failure must not claim "you have a draft saved" when nothing is.
      const wrote = writeDraft(draftKey, serializer())
      if (wrote) setHasDraft(true)
      draftTimerRef.current = null
    }, draftDebounceMs)
    return () => {
      if (draftTimerRef.current) {
        clearTimeout(draftTimerRef.current)
        draftTimerRef.current = null
      }
    }
  }, [when, draftKey, draftDebounceMs, draftTrigger])

  const restoreDraft = useCallback<() => T | null>(
    () => readDraft<T>(draftKey),
    [draftKey],
  )

  const discardDraft = useCallback(() => {
    if (!draftKey) return
    // Cancel any in-flight debounced write so a queued setTimeout can't
    // resurrect the draft immediately after we remove it.
    if (draftTimerRef.current) {
      clearTimeout(draftTimerRef.current)
      draftTimerRef.current = null
    }
    removeDraft(draftKey)
    setHasDraft(false)
  }, [draftKey])

  return { hasDraft, setHasDraft, draftTimerRef, restoreDraft, discardDraft }
}

/**
 * Intercept navigation while a form is dirty.
 *
 * Composes three layers:
 * 1. React Router `useBlocker` for in-app navigation
 * 2. `window.beforeunload` for tab close / reload
 * 3. (optional) localStorage draft persistence with debounced writes, exposing
 *    `hasDraft` + `restoreDraft` so the caller can offer draft restore on
 *    next visit.
 *
 * The caller is responsible for:
 * - Rendering a `<ConfirmDialog open={confirmOpen} onConfirm={proceed} onCancel={cancel} />`
 * - Calling `discardDraft()` after a successful save
 */
export function useUnsavedChangesGuard<T = unknown>({
  when,
  message = DEFAULT_MESSAGE,
  draftKey,
  draftData,
  draftTrigger,
  draftDebounceMs = 500,
  onDiscard,
}: UseUnsavedChangesGuardOptions): UseUnsavedChangesGuardResult<T> {
  const blocker = useBlocker(when)
  const confirmOpen = blocker.state === 'blocked'

  useBeforeUnloadGuard({ when })

  const draft = useDraftPersistence<T>({
    when,
    draftKey,
    draftData,
    draftTrigger,
    draftDebounceMs,
  })

  const proceed = useCallback(() => {
    if (blocker.state !== 'blocked') return
    // discardDraft() centralises the cancel-timer / remove-storage /
    // flip-state sequence; calling it here keeps proceed() from
    // reimplementing the same teardown.
    draft.discardDraft()
    onDiscard?.()
    blocker.proceed()
  }, [blocker, onDiscard, draft])

  const cancel = useCallback(() => {
    if (blocker.state === 'blocked') blocker.reset()
  }, [blocker])

  return {
    confirmOpen,
    proceed,
    cancel,
    message,
    hasDraft: draft.hasDraft,
    restoreDraft: draft.restoreDraft,
    discardDraft: draft.discardDraft,
  }
}
