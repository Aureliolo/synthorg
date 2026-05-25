/**
 * Side-effect-free module-scope state for the mcp-catalog package.
 *
 * Owns the debounce handle and generation counter used by
 * ``list-actions.ts::setSearchQuery``. Lives in its own file so
 * ``web/src/test-setup.tsx`` can call ``cancelPendingMcpCatalogSearch``
 * from the global ``afterEach`` without transitively loading
 * ``@/api/endpoints/mcp-catalog`` -> ``@/api/client`` (which captures a
 * live ``getCsrfToken`` reference before per-test ``vi.mock`` can
 * hoist; the in-file comment in ``test-setup.tsx`` explains why).
 */

let _searchDebounceHandle: ReturnType<typeof setTimeout> | null = null
let _searchGeneration = 0

export function getSearchDebounceHandle(): ReturnType<typeof setTimeout> | null {
  return _searchDebounceHandle
}

export function setSearchDebounceHandle(
  handle: ReturnType<typeof setTimeout> | null,
): void {
  _searchDebounceHandle = handle
}

export function bumpSearchGeneration(): number {
  return ++_searchGeneration
}

export function currentSearchGeneration(): number {
  return _searchGeneration
}

export function cancelPendingMcpCatalogSearch(): void {
  if (_searchDebounceHandle !== null) {
    clearTimeout(_searchDebounceHandle)
    _searchDebounceHandle = null
  }
  // Bump the generation so any timer callback that already
  // ran past its ``clearTimeout`` window (because it had been
  // queued onto the macrotask queue before cancellation) will
  // short-circuit on its own generation guard.
  _searchGeneration++
}
