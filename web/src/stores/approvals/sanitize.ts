import { sanitizeWsEnum, sanitizeWsString } from '@/utils/ws-sanitize'
import type {
  ApprovalResponse,
  EvidencePackage,
} from '@/api/types/approvals'
import {
  APPROVAL_RISK_LEVEL_VALUES,
  APPROVAL_SOURCE_VALUES,
  APPROVAL_STATUS_VALUES,
  URGENCY_LEVEL_VALUES,
} from '@/api/types/enums'
import { SIGNATURE_ALGORITHM_VALUES } from '@/api/types/approvals'

/** All metadata keys and values must be plain strings. */
function isStringStringRecord(
  value: unknown,
): value is Record<string, string> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return false
  }
  for (const [k, v] of Object.entries(value)) {
    if (typeof k !== 'string' || typeof v !== 'string') return false
  }
  return true
}

/** Every recommended-action entry must have the fields the sanitizer reads. */
function isRecommendedActionShape(value: unknown): boolean {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return false
  }
  const v = value as Record<string, unknown>
  return (
    typeof v['action_type'] === 'string'
    && typeof v['label'] === 'string'
    && typeof v['description'] === 'string'
    && typeof v['confirmation_required'] === 'boolean'
  )
}

/** Finite, non-negative integer (no NaN, no Infinity, no fractions, no negatives). */
function isNonNegInt(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0
}

/** Every signature entry must have id + algo + base64 bytes + timestamp + position. */
function isSignatureShape(value: unknown): boolean {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return false
  }
  const v = value as Record<string, unknown>
  return (
    typeof v['approver_id'] === 'string'
    && typeof v['algorithm'] === 'string'
    && typeof v['signature_bytes'] === 'string'
    && typeof v['signed_at'] === 'string'
    // ``chain_position`` must be a finite, non-negative integer;
    // reject NaN / Infinity / fractional / negative values that
    // ``typeof === 'number'`` would otherwise accept.
    && isNonNegInt(v['chain_position'])
  )
}

const EVIDENCE_REQUIRED_STRING_FIELDS = [
  'id',
  'title',
  'narrative',
  'source_agent_id',
  'risk_level',
  'created_at',
] as const

function isEvidencePackageBaseFields(v: Record<string, unknown>): boolean {
  for (const field of EVIDENCE_REQUIRED_STRING_FIELDS) {
    if (typeof v[field] !== 'string') return false
  }
  return (
    typeof v['is_fully_signed'] === 'boolean'
    && (v['task_id'] === null || typeof v['task_id'] === 'string')
  )
}

function isEvidencePackageCollections(v: Record<string, unknown>): boolean {
  return (
    Array.isArray(v['reasoning_trace'])
    && v['reasoning_trace'].every((line) => typeof line === 'string')
    && Array.isArray(v['recommended_actions'])
    && v['recommended_actions'].every(isRecommendedActionShape)
    && Array.isArray(v['signatures'])
    && v['signatures'].every(isSignatureShape)
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
  return (
    isEvidencePackageBaseFields(v)
    && isEvidencePackageCollections(v)
    && isStringStringRecord(v['metadata'])
    && isNonNegInt(v['signature_threshold'])
  )
}

/** Either ``null`` or a string -- used for the nullable decision/timing fields. */
function isNullableString(value: unknown): boolean {
  return value === null || typeof value === 'string'
}

/** Either ``null`` or a finite number -- ``seconds_remaining`` can be null on non-expiring approvals. */
function isNullableFiniteNumber(value: unknown): boolean {
  return value === null || Number.isFinite(value)
}

// Presence-as-string (enum fields included): a pre-upgrade frame
// missing one of these is rejected here rather than silently coerced
// by sanitizeWsEnum, while an unknown-but-present value still gets
// the forward-compat allowlist + fallback in sanitizeApproval.
const APPROVAL_REQUIRED_STRING_FIELDS = [
  'id',
  'status',
  'title',
  'risk_level',
  'source',
  'urgency_level',
  'action_type',
  'description',
  'requested_by',
  'created_at',
] as const

function isApprovalCoreFields(c: Record<string, unknown>): boolean {
  for (const field of APPROVAL_REQUIRED_STRING_FIELDS) {
    if (typeof c[field] !== 'string') return false
  }
  return true
}

function isApprovalNullableFields(c: Record<string, unknown>): boolean {
  return (
    isNullableString(c['task_id'])
    && isNullableString(c['decided_by'])
    && isNullableString(c['decision_reason'])
    && isNullableString(c['decided_at'])
    && isNullableString(c['expires_at'])
    && isNullableString(c['consumed_at'])
    && isNullableFiniteNumber(c['seconds_remaining'])
  )
}

/**
 * Type predicate: a WS payload object satisfies the {@link ApprovalResponse}
 * shape so consumers can use it without a cast. Enum-typed fields
 * (``status``, ``risk_level``) are validated against their declared
 * unions, and ``metadata`` must be a plain ``Record<string, string>``
 * (the contract on ``ApprovalResponse``) so malformed payloads can't
 * smuggle illegal values or non-string entries into the store.
 */
export function isApprovalShape(
  c: Record<string, unknown>,
): c is Record<string, unknown> & ApprovalResponse {
  // Enum fields (status, risk_level, urgency_level) are checked as
  // non-empty strings only; sanitizeApproval routes them through
  // sanitizeWsEnum which applies the allowlist + safe fallback when
  // a new backend value reaches the wire ahead of a frontend bump.
  // Treating an unknown enum here as "malformed payload" would defeat
  // the typed sanitizer's forward-compat contract.
  return (
    isApprovalCoreFields(c)
    && isStringStringRecord(c['metadata'])
    && isApprovalNullableFields(c)
    && isEvidencePackageShape(c['evidence_package'])
  )
}

function sanitizeRecommendedActions(
  actions: EvidencePackage['recommended_actions'],
): EvidencePackage['recommended_actions'] {
  return actions.map((a) => ({
    action_type: sanitizeWsString(a.action_type, 128) ?? '',
    label: sanitizeWsString(a.label, 128) ?? '',
    description: sanitizeWsString(a.description, 1024) ?? '',
    confirmation_required: a.confirmation_required,
  }))
}

function sanitizeSignatures(
  signatures: EvidencePackage['signatures'],
): EvidencePackage['signatures'] {
  return signatures.map((s) => ({
    approver_id: sanitizeWsString(s.approver_id, 128) ?? '',
    algorithm: sanitizeWsEnum(
      s.algorithm,
      SIGNATURE_ALGORITHM_VALUES,
      'ed25519',
      { maxLen: 64, field: 'evidence_package.signatures[].algorithm' },
    ),
    signature_bytes: sanitizeWsString(s.signature_bytes, 2048) ?? '',
    signed_at: sanitizeWsString(s.signed_at, 64) ?? '',
    chain_position: s.chain_position,
  }))
}

function sanitizeStringMap(
  map: Record<string, unknown>,
  keyCap: number,
  valueCap: number,
): Record<string, string> {
  // The shape guards (``isStringStringRecord``) have already verified
  // every value is a string at the ingress boundary, so the inner
  // coercion is a no-op at runtime; the wider parameter type lets us
  // accept ``ApprovalResponse.metadata`` (typed as
  // ``Record<string, unknown>`` in the generated DTO) without an
  // extra cast at each callsite.
  const out: Record<string, string> = {}
  for (const [key, value] of Object.entries(map)) {
    const safeKey = sanitizeWsString(key, keyCap) ?? ''
    if (!safeKey) continue
    out[safeKey] = sanitizeWsString(
      typeof value === 'string' ? value : '',
      valueCap,
    ) ?? ''
  }
  return out
}

function sanitizeReasoningTrace(lines: readonly string[]): string[] {
  return lines
    .map((line) => sanitizeWsString(line, 2048) ?? '')
    .filter((line) => line.length > 0)
}

function sanitizeEvidenceStrings(pkg: EvidencePackage) {
  return {
    id: sanitizeWsString(pkg.id, 128) ?? '',
    title: sanitizeWsString(pkg.title, 256) ?? '',
    narrative: sanitizeWsString(pkg.narrative, 4096) ?? '',
    source_agent_id: sanitizeWsString(pkg.source_agent_id, 128) ?? '',
    created_at: sanitizeWsString(pkg.created_at, 64) ?? '',
    task_id: pkg.task_id === null
      ? null
      : sanitizeWsString(pkg.task_id, 128) ?? '',
    risk_level: sanitizeWsEnum(
      pkg.risk_level,
      APPROVAL_RISK_LEVEL_VALUES,
      'low',
      { maxLen: 64, field: 'evidence_package.risk_level' },
    ),
  }
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
  return {
    ...sanitizeEvidenceStrings(pkg),
    reasoning_trace: sanitizeReasoningTrace(pkg.reasoning_trace),
    recommended_actions: sanitizeRecommendedActions(pkg.recommended_actions),
    metadata: sanitizeStringMap(pkg.metadata, 64, 512),
    signature_threshold: pkg.signature_threshold,
    signatures: sanitizeSignatures(pkg.signatures),
    is_fully_signed: pkg.is_fully_signed,
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
export function sanitizeApproval(c: ApprovalResponse): ApprovalResponse {
  // Preserve the ``string | null`` contract: if sanitization strips a
  // non-null value down to empty, report ``null`` rather than an
  // empty string the UI would treat as a real value.
  const sanitizeNullable = (
    value: string | null,
    cap: number,
  ): string | null =>
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
    risk_level: sanitizeWsEnum(
      c.risk_level,
      APPROVAL_RISK_LEVEL_VALUES,
      'low',
      { maxLen: 64, field: 'approval.risk_level' },
    ),
    source: sanitizeWsEnum(
      c.source,
      APPROVAL_SOURCE_VALUES,
      'review_gate',
      { maxLen: 64, field: 'approval.source' },
    ),
    status: sanitizeWsEnum(
      c.status,
      APPROVAL_STATUS_VALUES,
      'pending',
      { maxLen: 64, field: 'approval.status' },
    ),
    task_id: sanitizeNullable(c.task_id, 128),
    metadata: sanitizeStringMap(c.metadata, 64, 512),
    decided_by: sanitizeNullable(c.decided_by, 128),
    decision_reason: sanitizeNullable(c.decision_reason, 2048),
    created_at: sanitizeWsString(c.created_at, 64) ?? '',
    decided_at: sanitizeNullable(c.decided_at, 64),
    expires_at: sanitizeNullable(c.expires_at, 64),
    consumed_at: sanitizeNullable(c.consumed_at, 64),
    evidence_package: sanitizeEvidencePackage(c.evidence_package),
    seconds_remaining: c.seconds_remaining,
    urgency_level: sanitizeWsEnum(
      c.urgency_level,
      URGENCY_LEVEL_VALUES,
      'normal',
      { maxLen: 64, field: 'approval.urgency_level' },
    ),
  }
}
