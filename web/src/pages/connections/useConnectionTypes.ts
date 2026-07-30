import { useEffect } from 'react'
import { webhookSecretFieldFor } from '@/api/types/integrations'
import { useConnectionsStore } from '@/stores/connections'
import type { ConnectionType, ConnectionTypeMetadata } from '@/api/types/integrations'

/**
 * The connection-type registry, hydrated by whoever needs it.
 *
 * Every surface on this page renders from the registry (the type badge, the
 * filter labels, the form's field list, the receipts cross-link), so each one
 * asks for it directly rather than inheriting whatever a sibling happened to
 * fetch. The store's fetch is idempotent and single-flighted, so N consumers
 * still make one request; the point is that a consumer rendered without the
 * form no longer silently reads an empty registry and renders the wrong thing.
 *
 * Pure API consumer: re-fetched on mount, never persisted client-side.
 *
 * @returns The registry, empty until the first fetch settles.
 */
export function useConnectionTypes(): readonly ConnectionTypeMetadata[] {
  const connectionTypes = useConnectionsStore((s) => s.connectionTypes)
  const fetchConnectionTypes = useConnectionsStore((s) => s.fetchConnectionTypes)
  useEffect(() => {
    void fetchConnectionTypes()
  }, [fetchConnectionTypes])
  return connectionTypes
}

/**
 * The credential field this type's webhook signing secret goes in, if any.
 *
 * Answers the "can this connection ever receive a webhook" question without a
 * caller having to source the right registry array first, which is the part that
 * was easy to get wrong: passing a stale or empty array reads as "no webhooks"
 * rather than as an error.
 *
 * @returns The field name, or `null` when the type can never receive a webhook.
 */
export function useWebhookSecretField(type: ConnectionType | null): string | null {
  const connectionTypes = useConnectionTypes()
  if (type === null) return null
  return webhookSecretFieldFor(type, connectionTypes)
}
