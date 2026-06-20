/**
 * Build the `Idempotency-Key` header the backend requires on its retry-safe
 * mutating endpoints (approvals approve/reject, backup create/restore).
 *
 * A blank or whitespace-only key is treated as absent and a fresh UUID is
 * minted, so a first-time submission still satisfies the server's required
 * `min_length=1` header without forcing every caller to supply one. A `??`
 * fallback would forward an empty string through to the server, which then
 * rejects the request as a 400, so the key is trimmed before the check.
 */
export function idempotencyKeyHeader(idempotencyKey?: string): { 'Idempotency-Key': string } {
  const trimmed = idempotencyKey?.trim()
  const key = trimmed && trimmed.length > 0 ? trimmed : crypto.randomUUID()
  return { 'Idempotency-Key': key }
}
