/**
 * The `cited_records` wire contract and its defensive parser.
 *
 * Lives in its own module so both the buffered endpoint (`meta.ts`) and the
 * streaming consumer (`meta-stream.ts`) can share the type and the guard
 * without importing each other (which would form a dependency cycle).
 */

import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('cited-records')

/** One org-state record the chat answer is grounded in. */
export interface CitedRecord {
  kind: 'task' | 'project' | 'approval'
  record_id: string
  label: string
  status: string
}

const CITED_KINDS = new Set<CitedRecord['kind']>(['task', 'project', 'approval'])

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isCitedRecord(entry: unknown): entry is CitedRecord {
  return (
    isRecord(entry) &&
    typeof entry['kind'] === 'string' &&
    CITED_KINDS.has(entry['kind'] as CitedRecord['kind']) &&
    typeof entry['record_id'] === 'string' &&
    typeof entry['label'] === 'string' &&
    typeof entry['status'] === 'string'
  )
}

/**
 * Validate a wire `cited_records` array, dropping (and warning on) any entry
 * that doesn't match the contract. Shared by the streaming complete frame and
 * the buffered `postChat` response so both enter the UI through one guard.
 */
export function parseCitedRecords(value: unknown): CitedRecord[] {
  if (!Array.isArray(value)) return []
  const records: CitedRecord[] = []
  for (const entry of value) {
    if (isCitedRecord(entry)) {
      records.push(entry)
    } else {
      log.warn('Dropping malformed cited_record entry', sanitizeForLog(entry))
    }
  }
  return records
}
