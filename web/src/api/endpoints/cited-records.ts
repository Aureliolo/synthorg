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

function isNonBlankString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

function isCitedRecord(entry: unknown): entry is CitedRecord {
  return (
    isRecord(entry) &&
    typeof entry['kind'] === 'string' &&
    CITED_KINDS.has(entry['kind'] as CitedRecord['kind']) &&
    // The backend models record_id / label / status as non-blank strings;
    // mirror that so a blank wire value never reaches the reference chips.
    isNonBlankString(entry['record_id']) &&
    isNonBlankString(entry['label']) &&
    isNonBlankString(entry['status'])
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
