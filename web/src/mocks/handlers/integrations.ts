/**
 * Integrations umbrella -- re-exports the split per-domain handler arrays
 * for backward compatibility with Storybook stories that import
 * `integrationsHandlers`.
 *
 * The canonical per-domain handlers now live in:
 *   - connections.ts
 *   - integration-health.ts
 *   - mcp-catalog.ts
 *   - oauth.ts
 *   - tunnel.ts
 */

import { connectionsList } from './connections'
import { integrationHealthList } from './integration-health'
import { mcpCatalogHandlers } from './mcp-catalog'
import { oauthHandlers } from './oauth'
import { tunnelHandlers } from './tunnel'

export { connectionsList } from './connections'
export { tunnelHandlers } from './tunnel'

/** Spread of every integrations-domain Storybook handler set. */
export const integrationsHandlers = [
  ...connectionsList,
  ...integrationHealthList,
  ...oauthHandlers,
  ...mcpCatalogHandlers,
  ...tunnelHandlers,
]
