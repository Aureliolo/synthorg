import { useMemo } from 'react'
import { isLocalUrl } from '@/utils/provider-locality'
import type { ProviderConfig } from '@/api/types/providers'

/**
 * Memoized provider-name -> locality map, recomputed only when *providers*
 * changes. Shared by the setup roster and summary so both flag the same
 * agents as local (free) and stay in sync if the classification changes.
 */
export function useProviderLocality(
  providers: Readonly<Record<string, ProviderConfig>>,
): Record<string, boolean> {
  return useMemo(
    () =>
      Object.fromEntries(
        Object.entries(providers).map(([name, cfg]) => [name, isLocalUrl(cfg.base_url)]),
      ),
    [providers],
  )
}
