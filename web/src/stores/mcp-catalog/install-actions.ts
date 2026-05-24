import {
  installMcpServer,
  uninstallMcpServer,
} from '@/api/endpoints/mcp-catalog'
import type { McpInstallResponse } from '@/api/types/integrations'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'
import type { InstallContext, McpCatalogGet, McpCatalogSet } from './types'

const log = createLogger('mcp-install')

const EMPTY_CONTEXT: InstallContext = {
  entryId: null,
  connectionName: null,
  errorMessage: null,
  result: null,
}

let _installGeneration = 0

function startInstallImpl(
  set: McpCatalogSet,
  get: McpCatalogGet,
  entryId: string,
): void {
  const entry = get().entries.find((e) => e.id === entryId)
  if (entry === undefined) {
    set({
      installFlow: 'error',
      installContext: {
        ...EMPTY_CONTEXT,
        entryId,
        errorMessage: 'Catalog entry not found',
      },
    })
    return
  }
  const requiresConnection = entry.required_connection_type !== null
  set({
    installFlow: requiresConnection ? 'picking-connection' : 'installing',
    installContext: { ...EMPTY_CONTEXT, entryId },
  })
}

async function confirmInstallImpl(
  set: McpCatalogSet,
  get: McpCatalogGet,
): Promise<McpInstallResponse | null> {
  const ctx = get().installContext
  if (ctx.entryId === null) return null
  const generation = ++_installGeneration
  set({ installFlow: 'installing' })
  try {
    const result = await installMcpServer({
      catalog_entry_id: ctx.entryId,
      connection_name: ctx.connectionName ?? undefined,
    })
    if (generation !== _installGeneration) return null
    const installed = new Set(get().installedEntryIds)
    installed.add(ctx.entryId)
    set({
      installFlow: 'done',
      installContext: { ...ctx, result, errorMessage: null },
      installedEntryIds: installed,
    })
    useToastStore.getState().add({
      variant: 'success',
      title: `${result.server_name} installed`,
      description:
        `${String(result.tool_count)} tools available after MCP bridge reload`,
    })
    return result
  } catch (err) {
    if (generation !== _installGeneration) return null
    log.error('MCP install failed:', getErrorMessage(err))
    set({
      installFlow: 'error',
      installContext: {
        ...ctx,
        errorMessage: getErrorMessage(err),
        result: null,
      },
    })
    useToastStore.getState().add({
      variant: 'error',
      title: 'MCP install failed',
      description: getErrorMessage(err),
    })
    return null
  }
}

async function uninstallImpl(
  set: McpCatalogSet,
  get: McpCatalogGet,
  entryId: string,
): Promise<boolean> {
  try {
    await uninstallMcpServer(entryId)
    const installed = new Set(get().installedEntryIds)
    installed.delete(entryId)
    set({ installedEntryIds: installed })
    useToastStore.getState().add({
      variant: 'success',
      title: 'MCP server uninstalled',
    })
    return true
  } catch (err) {
    log.error('MCP uninstall failed:', getErrorMessage(err))
    useToastStore.getState().add({
      variant: 'error',
      title: 'Uninstall failed',
      description: getErrorMessage(err),
    })
    return false
  }
}

export function createInstallActions(
  set: McpCatalogSet,
  get: McpCatalogGet,
) {
  return {
    startInstall: (entryId: string) => startInstallImpl(set, get, entryId),
    setInstallConnection: (connectionName: string | null) => {
      const ctx = get().installContext
      set({ installContext: { ...ctx, connectionName } })
    },
    confirmInstall: () => confirmInstallImpl(set, get),
    uninstall: (entryId: string) => uninstallImpl(set, get, entryId),
    resetInstall: () => {
      ++_installGeneration
      set({
        installFlow: 'idle',
        installContext: { ...EMPTY_CONTEXT },
      })
    },
  }
}
