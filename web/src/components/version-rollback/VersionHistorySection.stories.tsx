import type { Meta, StoryObj } from '@storybook/react-vite'
import { VersionHistorySection } from './VersionHistorySection'
import type { PaginatedResult } from '@/api/client'
import type {
  VersionDiffResponse,
  VersionHistoryClient,
  VersionSnapshot,
} from '@/api/endpoints/version-history'

type Snapshot = VersionSnapshot<Record<string, unknown>>

function buildSnapshot(version: number): Snapshot {
  return {
    entity_id: 'example-entity',
    version,
    content_hash: `hash-${version}`,
    saved_at: new Date(2026, 3, 28 - version, 9, 0, 0).toISOString(),
    saved_by: 'user-1',
    snapshot: { name: `state-v${version}`, capacity: 10 + version },
  }
}

function makeStubClient(
  versions: readonly number[],
  hasMore = false,
): VersionHistoryClient<Record<string, unknown>> {
  return {
    list: (): Promise<PaginatedResult<Snapshot>> => Promise.resolve({
      data: versions.map(buildSnapshot),
      limit: 25,
      nextCursor: hasMore ? 'next-cursor' : null,
      hasMore,
      pagination: {
        limit: 25,
        next_cursor: hasMore ? 'next-cursor' : null,
        has_more: hasMore,
      },
    }),
    get: (version: number) => Promise.resolve(buildSnapshot(version)),
    diff: (from: number, to: number): Promise<VersionDiffResponse> => Promise.resolve({
      from_version: from,
      to_version: to,
      entries: [
        { path: 'capacity', before: 10 + from, after: 10 + to },
        { path: 'name', before: `state-v${from}`, after: `state-v${to}` },
      ],
    }),
    rollback: () => Promise.resolve(buildSnapshot(versions[0] ?? 1)),
  }
}

const meta = {
  title: 'VersionRollback/VersionHistorySection',
  component: VersionHistorySection<Record<string, unknown>>,
} satisfies Meta<typeof VersionHistorySection<Record<string, unknown>>>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: {
    client: makeStubClient([5, 4, 3, 2, 1]),
    title: 'Version history',
    description: 'Pick two versions to diff, or one to roll back.',
    rollbackSupported: true,
  },
}

export const ReadOnly: Story = {
  args: {
    client: makeStubClient([5, 4, 3, 2, 1]),
    title: 'Budget config history',
    description: 'Read-only audit trail.',
  },
}

export const WithLoadMore: Story = {
  args: {
    client: makeStubClient([5, 4, 3], /*hasMore=*/ true),
    title: 'Version history',
    rollbackSupported: true,
  },
}

export const Empty: Story = {
  args: {
    client: makeStubClient([]),
    title: 'Version history',
    rollbackSupported: true,
    emptyTitle: 'No versions yet',
    emptyDescription: 'Versions appear after the first edit.',
  },
}
