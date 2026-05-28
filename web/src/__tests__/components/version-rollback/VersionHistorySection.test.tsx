import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { VersionHistorySection } from '@/components/version-rollback/VersionHistorySection'
import type {
  ReadOnlyVersionHistoryClient,
  VersionSnapshot,
} from '@/api/endpoints/version-history'
import type { PaginatedResult } from '@/api/client'

function snap(version: number): VersionSnapshot<Record<string, unknown>> {
  return {
    entity_id: 'entity-1',
    version,
    content_hash: 'h',
    saved_at: '2026-04-19T00:00:00Z',
    saved_by: 'user-1',
    snapshot: {},
  }
}

function twoVersionClient(
  diff: () => Promise<never>,
): ReadOnlyVersionHistoryClient<Record<string, unknown>> {
  const page: PaginatedResult<VersionSnapshot<Record<string, unknown>>> = {
    data: [snap(2), snap(1)],
    limit: 25,
    nextCursor: null,
    hasMore: false,
    pagination: { limit: 25, next_cursor: null, has_more: false },
  }
  return {
    list: () => Promise.resolve(page),
    get: (version) => Promise.resolve(snap(version)),
    diff,
  }
}

describe('VersionHistorySection diff gating', () => {
  it('renders non-interactive rows and never fetches a diff when diffSupported is false', async () => {
    // diff must never be called for a diff-less backend (e.g. roles).
    const diff = vi.fn<() => Promise<never>>(() =>
      Promise.reject(new Error('diff should not be called')),
    )
    render(
      <VersionHistorySection
        client={twoVersionClient(diff)}
        diffSupported={false}
        title="Role versions"
      />,
    )

    // Rows render...
    expect(await screen.findByText('v1')).toBeInTheDocument()
    expect(screen.getByText('v2')).toBeInTheDocument()
    // ...but as static text, not selectable buttons, so the two-click
    // compare that would hit the (absent) diff endpoint cannot fire.
    const timeline = screen.getByRole('list', { name: /version history/i })
    expect(within(timeline).queryByRole('button')).toBeNull()
    expect(diff).not.toHaveBeenCalled()
  })
})
