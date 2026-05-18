import { create } from 'zustand'
import * as approvalsApi from '@/api/endpoints/approvals'
import { sanitizeWsEnum, sanitizeWsString } from '@/utils/ws-sanitize'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { isObject } from '@/utils/type-guards'
import { createLogger } from '@/lib/logger'
import type {
  ApprovalFilters,
  ApprovalResponse,
  ApproveRequest,
  EvidencePackage,
  RejectRequest,
} from '@/api/types/approvals'
import {
  APPROVAL_RISK_LEVEL_VALUES,
  APPROVAL_SOURCE_VALUES,
  APPROVAL_STATUS_VALUES,
  URGENCY_LEVEL_VALUES,
} from '@/api/types/enums'
import { SIGNATURE_ALGORITHM_VALUES } from '@/api/types/approvals'
import type { WsEvent } from '@/api/types/websocket'

const log = createLogger('approvals')

/** All metadata keys and values must be plain strings. */
function isStringStringRecord(value: unknown): value is Record<string, string> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  for (const [k, v] of Object.entries(value)) {
    if (typeof k !== 'string' || typeof v !== 'string') return false
  }
  return true
}

/** Every recommended-action entry must have the fields the sanitizer reads. */
function isRecommendedActionShape(value: unknown): boolean {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const v = value as Record<string, unknown>
  return (
    typeof v.action_type === 'string' &&
    typeof v.label === 'string' &&
    typeof v.description === 'string' &&
    typeof v.confirmation_required === 'boolean'
  )
}

/** Finite, non-negative integer (no NaN, no Infinity, no fractions, no negatives). */
function isNonNegInt(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0
}

/** Every signature entry must have id + algo + base64 bytes + timestamp + position. */
function isSignatureShape(value: unknown): boolean {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const v = value as Record<string, unknown>
  return (
    typeof v.approver_id === 'string' &&
    typeof v.algorithm === 'string' &&
    typeof v.signature_bytes === 'string' &&
    typeof v.signed_at === 'string' &&
    // ``chain_position`` must be a finite, non-negative integer;
    // reject NaN / Infinity / fractional / negative values that
    // ``typeof === 'number'`` would otherwise accept.
    isNonNegInt(v.chain_position)
  )
}

/**
 * ``evidence_package`` is nullable (approvals without structured
 * evidence) but when present must carry every field
 * ``sanitizeEvidencePackage`` dereferences. Without this guard a
 * malformed payload like ``{reasoning_trace: null}`` or
 * ``{signatures: [null]}`` would pass ``isApprovalShape`` and blow
 * up inside the sanitizer's ``map`` / ``Object.entries`` calls.
 */
function isEvidencePackageShape(value: unknown): boolean {
  if (value === null) return true
  if (typeof value !== 'object' || Array.isArray(value)) return false
  const v = value as Record<string, unknown>
  if (typeof v.id !== 'string') return false
  if (typeof v.title !== 'string') return false
  if (typeof v.narrative !== 'string') return false
  if (
    !Array.isArray(v.reasoning_trace) ||
    !v.reasoning_trace.every((line) => typeof line === 'string')
  ) {
    return false
  }
  if (
    !Array.isArray(v.recommended_actions) ||
    !v.recommended_actions.every(isRecommendedActionShape)
  ) {
    return false
  }
  if (typeof v.source_agent_id !== 'string') return false
  if (v.task_id !== null && typeof v.task_id !== 'string') return false
  // Accept any non-empty string for risk_level here -- sanitizeWsEnum
  // applies the allowlist + fallback in sanitizeEvidencePackage. A
  // hard reject at this layer would drop the whole payload when the
  // backend rolls out a new enum value, defeating the point of the
  // typed sanitizer's forward-compat contract.
  if (typeof v.risk_level !== 'string') return false
  // ``EvidencePackage.metadata`` is a string->unknown map on the
  // declared TS side, but the server emits it with string values
  // only. Validate that shape here so ``sanitizeEvidencePackage``
  // can trust the values when it calls ``sanitizeWsString`` on them.
  if (!isStringStringRecord(v.metadata)) return false
  // ``signature_threshold`` is the minimum number of valid signatures
  // required; a fractional, negative, NaN, or Infinity value would
  // misgate ``is_fully_signed`` downstream and poison any UI comparing
  // against ``signatures.length``.
  if (!isNonNegInt(v.signature_threshold)) return false
  if (
    !Array.isArray(v.signatures) ||
    !v.signatures.every(isSignatureShape)
  ) {
    return false
  }
  if (typeof v.is_fully_signed !== 'boolean') return false
  if (typeof v.created_at !== 'string') return false
  return true
}

/**
 * Type predicate: a WS payload object satisfies the {@link ApprovalResponse}
 * shape so consumers can use it without a cast. Enum-typed fields
 * (``status``, ``risk_level``) are validated against their declared
 * unions, and ``metadata`` must be a plain ``Record<string, string>``
 * (the contract on ``ApprovalResponse``) so malformed payloads can't
 * smuggle illegal values or non-string entries into the store.
 */
/** Either ``null`` or a string -- used for the nullable decision/timing fields. */
function isNullableString(value: unknown): boolean {
  return value === null || typeof value === 'string'
}

/** Either ``null`` or a finite number -- ``seconds_remaining`` can be null on non-expiring approvals. */
function isNullableFiniteNumber(value: unknown): boolean {
  return value === null || Number.isFinite(value)
}

function isApprovalShape(
  c: Record<string, unknown>,
): c is Record<string, unknown> & ApprovalResponse {
  // Enum fields (status, risk_level, urgency_level) are checked as
  // non-empty strings only; sanitizeApproval routes them through
  // sanitizeWsEnum which applies the allowlist + safe fallback when
  // a new backend value reaches the wire ahead of a frontend bump.
  // Treating an unknown enum here as "malformed payload" would defeat
  // the typed sanitizer's forward-compat contract.
  return (
    typeof c.id === 'string' &&
    typeof c.status === 'string' &&
    typeof c.title === 'string' &&
    typeof c.risk_level === 'string' &&
    typeof c.urgency_level === 'string' &&
    typeof c.action_type === 'string' &&
    typeof c.description === 'string' &&
    typeof c.requested_by === 'string' &&
    typeof c.created_at === 'string' &&
    isStringStringRecord(c.metadata) &&
    // Nullable decision + timing fields: sanitizeApproval dereferences
    // each with sanitizeWsString, which needs a guaranteed ``string |
    // null`` -- otherwise an ``undefined`` on the wire would widen the
    // return type and the store would persist missing-vs-null as the
    // same thing.
    isNullableString(c.task_id) &&
    isNullableString(c.decided_by) &&
    isNullableString(c.decision_reason) &&
    isNullableString(c.decided_at) &&
    isNullableString(c.expires_at) &&
    isNullableFiniteNumber(c.seconds_remaining) &&
    isEvidencePackageShape(c.evidence_package)
  )
}

/**
 * Recursively sanitize an ``EvidencePackage`` -- title, narrative,
 * reasoning-trace lines, recommended-action fields, signature
 * entries, and nested id/timestamp fields all arrive over the wire
 * and must be scrubbed before reaching the store. Returns ``null``
 * unchanged (an approval without structured evidence).
 */
function sanitizeEvidencePackage(
  pkg: EvidencePackage | null,
): EvidencePackage | null {
  if (pkg === null) return null
  // ``isEvidencePackageShape`` has already enforced
  // ``Record<string, string>`` via ``isStringStringRecord``, so every
  // ``value`` below is guaranteed to be a string -- no non-string
  // branch required.
  const pkgMetadata: Record<string, string> = {}
  for (const [key, value] of Object.entries(pkg.metadata ?? {})) {
    const safeKey = sanitizeWsString(key, 64) ?? ''
    if (!safeKey) continue
    pkgMetadata[safeKey] = sanitizeWsString(value, 512) ?? ''
  }
  return {
    id: sanitizeWsString(pkg.id, 128) ?? '',
    title: sanitizeWsString(pkg.title, 256) ?? '',
    narrative: sanitizeWsString(pkg.narrative, 4096) ?? '',
    reasoning_trace: pkg.reasoning_trace
      .map((line) => sanitizeWsString(line, 2048) ?? '')
      .filter((line) => line.length > 0),
    recommended_actions: pkg.recommended_actions.map((a) => ({
      action_type: sanitizeWsString(a.action_type, 128) ?? '',
      label: sanitizeWsString(a.label, 128) ?? '',
      description: sanitizeWsString(a.description, 1024) ?? '',
      confirmation_required: a.confirmation_required,
    })),
    source_agent_id: sanitizeWsString(pkg.source_agent_id, 128) ?? '',
    task_id:
      pkg.task_id === null ? null : sanitizeWsString(pkg.task_id, 128) ?? '',
    risk_level: sanitizeWsEnum(pkg.risk_level, APPROVAL_RISK_LEVEL_VALUES, 'low', {
      maxLen: 64,
      field: 'evidence_package.risk_level',
    }),
    metadata: pkgMetadata,
    signature_threshold: pkg.signature_threshold,
    signatures: pkg.signatures.map((s) => ({
      approver_id: sanitizeWsString(s.approver_id, 128) ?? '',
      algorithm: sanitizeWsEnum(s.algorithm, SIGNATURE_ALGORITHM_VALUES, 'ed25519', {
        maxLen: 64,
        field: 'evidence_package.signatures[].algorithm',
      }),
      signature_bytes: sanitizeWsString(s.signature_bytes, 2048) ?? '',
      signed_at: sanitizeWsString(s.signed_at, 64) ?? '',
      chain_position: s.chain_position,
    })),
    is_fully_signed: pkg.is_fully_signed,
    created_at: sanitizeWsString(pkg.created_at, 64) ?? '',
  }
}

/**
 * Return a sanitized copy of an ``ApprovalResponse`` with every
 * untrusted WS-origin string field (identifier, action type,
 * enum-typed labels, timestamps, decision fields, and every metadata
 * entry) routed through ``sanitizeWsString``. The shape guard above
 * has already verified the required fields are non-empty strings at
 * ingress time; structurally required fields fall back to ``''`` if
 * sanitization drops them. Optional string fields preserve their
 * ``null``/``undefined`` signal so downstream code can still branch
 * on presence.
 */
function sanitizeApproval(c: ApprovalResponse): ApprovalResponse {
  const metadata: Record<string, string> = {}
  for (const [key, value] of Object.entries(c.metadata)) {
    const safeKey = sanitizeWsString(key, 64) ?? ''
    const safeValue = sanitizeWsString(value, 512) ?? ''
    if (safeKey) metadata[safeKey] = safeValue
  }
  // Preserve the ``string | null`` contract: if sanitization strips a
  // non-null value down to empty, report ``null`` rather than an
  // empty string the UI would treat as a real value.
  const sanitizeNullable = (value: string | null, cap: number): string | null =>
    value === null ? null : sanitizeWsString(value, cap) || null
  // Build the returned ``ApprovalResponse`` explicitly rather than
  // spreading ``c``: a spread would pass through the deeply-nested
  // ``evidence_package`` (plus any future string fields) with raw,
  // unsanitized WS content reaching the store.
  return {
    id: sanitizeWsString(c.id, 128) ?? '',
    action_type: sanitizeWsString(c.action_type, 128) ?? '',
    title: sanitizeWsString(c.title, 256) ?? '',
    description: sanitizeWsString(c.description, 2048) ?? '',
    requested_by: sanitizeWsString(c.requested_by, 128) ?? '',
    risk_level: sanitizeWsEnum(c.risk_level, APPROVAL_RISK_LEVEL_VALUES, 'low', {
      maxLen: 64,
      field: 'approval.risk_level',
    }),
    source: sanitizeWsEnum(c.source, APPROVAL_SOURCE_VALUES, 'review_gate', {
      maxLen: 64,
      field: 'approval.source',
    }),
    status: sanitizeWsEnum(c.status, APPROVAL_STATUS_VALUES, 'pending', {
      maxLen: 64,
      field: 'approval.status',
    }),
    task_id: sanitizeNullable(c.task_id, 128),
    metadata,
    decided_by: sanitizeNullable(c.decided_by, 128),
    decision_reason: sanitizeNullable(c.decision_reason, 2048),
    created_at: sanitizeWsString(c.created_at, 64) ?? '',
    decided_at: sanitizeNullable(c.decided_at, 64),
    expires_at: sanitizeNullable(c.expires_at, 64),
    evidence_package: sanitizeEvidencePackage(c.evidence_package),
    seconds_remaining: c.seconds_remaining,
    urgency_level: sanitizeWsEnum(c.urgency_level, URGENCY_LEVEL_VALUES, 'normal', {
      maxLen: 64,
      field: 'approval.urgency_level',
    }),
  }
}

interface ApprovalsState {
  // Data
  approvals: ApprovalResponse[]
  selectedApproval: ApprovalResponse | null
  total: number

  // Loading
  loading: boolean
  loadingDetail: boolean
  error: string | null
  detailError: string | null

  // CRUD
  fetchApprovals: (filters?: ApprovalFilters) => Promise<void>
  fetchApproval: (id: string) => Promise<void>
  approveOne: (id: string, data?: ApproveRequest) => Promise<ApprovalResponse | null>
  rejectOne: (id: string, data: RejectRequest) => Promise<ApprovalResponse | null>

  // Real-time
  handleWsEvent: (event: WsEvent) => void

  // Optimistic helpers
  pendingTransitions: Set<string>
  optimisticApprove: (id: string) => () => void
  optimisticReject: (id: string) => () => void
  upsertApproval: (approval: ApprovalResponse) => void

  // Batch selection
  selectedIds: Set<string>
  toggleSelection: (id: string) => void
  selectAllInGroup: (ids: string[]) => void
  deselectAllInGroup: (ids: string[]) => void
  clearSelection: () => void

  // Batch operations
  batchApprove: (ids: string[], comment?: string) => Promise<{ succeeded: number; failed: number; failedReasons: string[] }>
  batchReject: (ids: string[], reason: string) => Promise<{ succeeded: number; failed: number; failedReasons: string[] }>

  // Lifecycle (#1600 Phase 5). No-op today; future timers / listeners
  // should be torn down here so the global ``afterEach`` in
  // ``web/src/test-setup.tsx`` releases them deterministically.
  dispose: () => void
}

const pendingTransitions = new Set<string>()
const MAX_BATCH_SIZE = 50

/** Clear module-level pendingTransitions -- test-only. */
export function _resetPendingTransitions(): void {
  pendingTransitions.clear()
}

let listRequestSeq = 0
let detailRequestSeq = 0
// Generation token bumped by ``dispose()``. Pairs with the seq
// counters so a still-in-flight request from before a dispose can
// never collide with fresh post-dispose seq values (the captured
// epoch always differs from the current epoch after a bump).
let requestEpoch = 0

/** Reset module-level detailRequestSeq -- test-only. */
export function _resetDetailRequestSeq(): void {
  detailRequestSeq = 0
}

export const useApprovalsStore = create<ApprovalsState>()((set, get) => ({
  approvals: [],
  selectedApproval: null,
  total: 0,
  loading: false,
  loadingDetail: false,
  error: null,
  detailError: null,
  pendingTransitions,
  selectedIds: new Set<string>(),

  fetchApprovals: async (filters) => {
    const epoch = requestEpoch
    const seq = ++listRequestSeq
    set({ loading: true, error: null })
    try {
      const result = await approvalsApi.listApprovals(filters)
      if (epoch !== requestEpoch || seq !== listRequestSeq) return // stale
      // Merge: preserve items with pending optimistic transitions
      const merged = result.data.map((serverItem) => {
        if (pendingTransitions.has(serverItem.id)) {
          const existing = get().approvals.find((a) => a.id === serverItem.id)
          return existing ?? serverItem
        }
        return serverItem
      })
      // Prune selectedIds: only keep IDs that are still pending
      const pendingIds = new Set(merged.filter((a) => a.status === 'pending').map((a) => a.id))
      const prevSelected = get().selectedIds
      const prunedSelected = [...prevSelected].some((sid) => !pendingIds.has(sid))
        ? new Set([...prevSelected].filter((sid) => pendingIds.has(sid)))
        : prevSelected
      // Sync selectedApproval with fresh data if drawer is open
      const currentSelected = get().selectedApproval
      const freshSelected = currentSelected ? merged.find((a) => a.id === currentSelected.id) ?? currentSelected : null
      set({ approvals: merged, total: merged.length, loading: false, selectedIds: prunedSelected, selectedApproval: freshSelected })
    } catch (err) {
      if (epoch !== requestEpoch || seq !== listRequestSeq) return
      log.warn('Failed to fetch approvals', sanitizeForLog(err))
      set({ loading: false, error: getErrorMessage(err) })
    }
  },

  fetchApproval: async (id) => {
    const epoch = requestEpoch
    const seq = ++detailRequestSeq
    set({ loadingDetail: true, detailError: null, selectedApproval: null })
    try {
      const approval = await approvalsApi.getApproval(id)
      if (epoch !== requestEpoch || seq !== detailRequestSeq) return // stale
      set({ selectedApproval: approval, loadingDetail: false, detailError: null })
    } catch (err) {
      if (epoch !== requestEpoch || seq !== detailRequestSeq) return // stale
      log.warn('Failed to fetch approval detail', sanitizeForLog(err))
      set({ loadingDetail: false, detailError: getErrorMessage(err) })
    }
  },

  approveOne: async (id, data) => {
    try {
      const approval = await approvalsApi.approveApproval(id, data)
      get().upsertApproval(approval)
      useToastStore.getState().add({
        variant: 'success',
        title: 'Approval granted',
      })
      return approval
    } catch (err) {
      log.error('Approve approval failed', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        title: 'Could not approve',
        description: getErrorMessage(err),
      })
      return null
    }
  },

  rejectOne: async (id, data) => {
    try {
      const approval = await approvalsApi.rejectApproval(id, data)
      get().upsertApproval(approval)
      useToastStore.getState().add({
        variant: 'success',
        title: 'Approval rejected',
      })
      return approval
    } catch (err) {
      log.error('Reject approval failed', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        title: 'Could not reject',
        description: getErrorMessage(err),
      })
      return null
    }
  },

  handleWsEvent: (event) => {
    // Guard the envelope first: ``event.payload`` is typed as
    // ``Record<string, unknown>`` on the wire but a malformed broker
    // could still send ``null`` or a non-object, in which case
    // reading ``.approval`` off it would throw. Drop those frames
    // silently rather than letting the WS pipeline crash.
    if (!isObject(event.payload)) return
    const payload = event.payload
    // ``isObject`` narrows in one step instead of the inline hand-rolled
    // ``typeof === 'object' && !== null && !Array.isArray`` chain
    // followed by a downstream ``as`` cast.
    if (isObject(payload.approval)) {
      const candidate: Record<string, unknown> = payload.approval
      if (isApprovalShape(candidate)) {
        // Sanitize *before* the pendingTransitions check so a frame
        // whose id carries control/bidi chars can't bypass the
        // optimistic-transition gate (which keys off the raw id) and
        // then sanitize to the plain id to overwrite a real approval.
        // Mutation = the wire id carried chars we stripped, so we
        // can't trust it to point at the intended record.
        const sanitized = sanitizeApproval(candidate)
        if (!sanitized.id || sanitized.id !== candidate.id) {
          log.error(
            'Approval payload lost or mutated id during sanitization, skipping upsert',
            sanitizeForLog({ raw_id: candidate.id, sanitized_id: sanitized.id }),
          )
          return
        }
        if (pendingTransitions.has(sanitized.id)) return
        get().upsertApproval(sanitized)
      } else {
        log.error('Received malformed approval payload, skipping upsert', {
          id: sanitizeForLog(candidate.id),
          hasTitle: typeof candidate.title === 'string',
          hasStatus: typeof candidate.status === 'string',
        })
      }
    }
  },

  optimisticApprove: (id) => {
    const approvals = get().approvals
    const idx = approvals.findIndex((a) => a.id === id)
    if (idx === -1) {
      log.warn('optimisticApprove: approval not found in store', id)
      return () => {}
    }
    pendingTransitions.add(id)
    const prevSelectedIds = get().selectedIds
    const hadSelection = prevSelectedIds.has(id)
    const newSelectedIds = new Set(prevSelectedIds)
    newSelectedIds.delete(id)
    const oldApproval = approvals[idx]!
    const updated = { ...oldApproval, status: 'approved' as const, decided_at: new Date().toISOString() }
    const newApprovals = [...approvals]
    newApprovals[idx] = updated
    const selectedApproval = get().selectedApproval?.id === id ? updated : get().selectedApproval
    set({ approvals: newApprovals, selectedIds: newSelectedIds, selectedApproval })
    return () => {
      pendingTransitions.delete(id)
      set((s) => {
        const currentApprovals = [...s.approvals]
        const currentIdx = currentApprovals.findIndex((a) => a.id === id)
        if (currentIdx !== -1) currentApprovals[currentIdx] = oldApproval
        const restoredIds = hadSelection ? new Set([...s.selectedIds, id]) : s.selectedIds
        const restoredSelected = s.selectedApproval?.id === id ? oldApproval : s.selectedApproval
        return { approvals: currentApprovals, selectedIds: restoredIds, selectedApproval: restoredSelected }
      })
    }
  },

  optimisticReject: (id) => {
    const approvals = get().approvals
    const idx = approvals.findIndex((a) => a.id === id)
    if (idx === -1) {
      log.warn('optimisticReject: approval not found in store', id)
      return () => {}
    }
    pendingTransitions.add(id)
    const prevSelectedIds = get().selectedIds
    const hadSelection = prevSelectedIds.has(id)
    const newSelectedIds = new Set(prevSelectedIds)
    newSelectedIds.delete(id)
    const oldApproval = approvals[idx]!
    const updated = { ...oldApproval, status: 'rejected' as const, decided_at: new Date().toISOString() }
    const newApprovals = [...approvals]
    newApprovals[idx] = updated
    const selectedApproval = get().selectedApproval?.id === id ? updated : get().selectedApproval
    set({ approvals: newApprovals, selectedIds: newSelectedIds, selectedApproval })
    return () => {
      pendingTransitions.delete(id)
      set((s) => {
        const currentApprovals = [...s.approvals]
        const currentIdx = currentApprovals.findIndex((a) => a.id === id)
        if (currentIdx !== -1) currentApprovals[currentIdx] = oldApproval
        const restoredIds = hadSelection ? new Set([...s.selectedIds, id]) : s.selectedIds
        const restoredSelected = s.selectedApproval?.id === id ? oldApproval : s.selectedApproval
        return { approvals: currentApprovals, selectedIds: restoredIds, selectedApproval: restoredSelected }
      })
    }
  },

  upsertApproval: (approval) => {
    pendingTransitions.delete(approval.id)
    set((s) => {
      const idx = s.approvals.findIndex((a) => a.id === approval.id)
      const newApprovals = idx === -1 ? [approval, ...s.approvals] : [...s.approvals]
      if (idx !== -1) newApprovals[idx] = approval
      const selectedApproval = s.selectedApproval?.id === approval.id ? approval : s.selectedApproval
      const newSelectedIds = approval.status !== 'pending' && s.selectedIds.has(approval.id)
        ? new Set([...s.selectedIds].filter((sid) => sid !== approval.id))
        : s.selectedIds
      return {
        approvals: newApprovals,
        selectedApproval,
        selectedIds: newSelectedIds,
        ...(idx === -1 ? { total: s.total + 1 } : {}),
      }
    })
  },

  toggleSelection: (id) => {
    set((s) => {
      const next = new Set(s.selectedIds)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return { selectedIds: next }
    })
  },

  selectAllInGroup: (ids) => {
    set((s) => {
      const next = new Set(s.selectedIds)
      for (const id of ids) next.add(id)
      return { selectedIds: next }
    })
  },

  deselectAllInGroup: (ids) => {
    set((s) => {
      const next = new Set(s.selectedIds)
      for (const id of ids) next.delete(id)
      return { selectedIds: next }
    })
  },

  clearSelection: () => {
    set({ selectedIds: new Set() })
  },

  batchApprove: async (ids, comment) => {
    if (ids.length > MAX_BATCH_SIZE) {
      return { succeeded: 0, failed: ids.length, failedReasons: [`Batch size exceeds maximum of ${MAX_BATCH_SIZE}`] }
    }

    const rollbacks: Map<string, () => void> = new Map()

    for (const id of ids) {
      const rollback = get().optimisticApprove(id)
      rollbacks.set(id, rollback)
    }

    const results = await Promise.allSettled(
      ids.map((id) => approvalsApi.approveApproval(id, comment ? { comment } : undefined)),
    )

    let succeeded = 0
    let failed = 0
    const failedReasons: string[] = []
    for (let i = 0; i < results.length; i++) {
      const result = results[i]!
      const id = ids[i]!
      if (result.status === 'fulfilled') {
        get().upsertApproval(result.value)
        succeeded++
      } else {
        const rollback = rollbacks.get(id)
        if (rollback) rollback()
        failedReasons.push(getErrorMessage(result.reason))
        failed++
      }
    }

    if (failed === 0) {
      get().clearSelection()
    }
    // (failed IDs are already rolled back and restored to selectedIds by the targeted rollback)
    return { succeeded, failed, failedReasons }
  },

  batchReject: async (ids, reason) => {
    if (ids.length > MAX_BATCH_SIZE) {
      return { succeeded: 0, failed: ids.length, failedReasons: [`Batch size exceeds maximum of ${MAX_BATCH_SIZE}`] }
    }

    const rollbacks: Map<string, () => void> = new Map()

    for (const id of ids) {
      const rollback = get().optimisticReject(id)
      rollbacks.set(id, rollback)
    }

    const results = await Promise.allSettled(
      ids.map((id) => approvalsApi.rejectApproval(id, { reason })),
    )

    let succeeded = 0
    let failed = 0
    const failedReasons: string[] = []
    for (let i = 0; i < results.length; i++) {
      const result = results[i]!
      const id = ids[i]!
      if (result.status === 'fulfilled') {
        get().upsertApproval(result.value)
        succeeded++
      } else {
        const rollback = rollbacks.get(id)
        if (rollback) rollback()
        failedReasons.push(getErrorMessage(result.reason))
        failed++
      }
    }

    if (failed === 0) {
      get().clearSelection()
    }
    // (failed IDs are already rolled back and restored to selectedIds by the targeted rollback)
    return { succeeded, failed, failedReasons }
  },
  dispose: () => {
    // Bump the generation token so any in-flight request from
    // before the dispose can never collide with post-dispose seq
    // values (the captured ``epoch`` will not match the new
    // ``requestEpoch``). Resetting the seq counters and the
    // optimistic-transition set keeps fresh calls starting from
    // clean state (#1600 Phase 5).
    requestEpoch += 1
    _resetPendingTransitions()
    _resetDetailRequestSeq()
    listRequestSeq = 0
  },
}))
