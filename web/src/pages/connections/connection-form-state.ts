import { type ConnectionType } from '@/api/types/integrations'

/**
 * Shared form-state shapes for the connection form. A leaf module both
 * ``useConnectionForm`` and ``connection-submit`` import from, so the submit
 * machinery can reference the form types without a hook <-> submit import cycle.
 */

export type Mode = 'create' | 'edit'

export interface ConnectionFormState {
  name: string
  type: ConnectionType | null
  topLevel: Record<string, string>
  credentials: Record<string, string>
  /** Non-secret fields stored on the connection record; editable after create. */
  metadata: Record<string, string>
  webhookRetention: string
  sensitive: boolean
}
