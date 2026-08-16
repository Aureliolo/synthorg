/** Admin audit-log entry types. */

// The ROW is what the endpoint returns: the entry plus the recorded agent's
// resolved name. The dashboard has no other audit shape.
export type { AuditEntryRow as AuditEntry } from './dtos.gen'
