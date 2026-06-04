import type { Meta, StoryObj } from '@storybook/react-vite'
import { VersionDiffDrawer } from './VersionDiffDrawer'
import type {
  VersionDiffResponse,
  VersionHistoryClient,
  VersionSnapshot,
} from '@/api/endpoints/version-history'

type SamplePayload = Record<string, unknown>

const sampleDiff: VersionDiffResponse = {
  from_version: 1,
  to_version: 2,
  entries: [
    {
      path: 'description',
      before: 'Reviews PRs',
      after: 'Reviews PRs and writes follow-up tasks',
    },
    {
      path: 'metadata.tags',
      before: ['review'],
      after: ['review', 'planning'],
    },
  ],
}

function makeClient(
  override?: Partial<VersionHistoryClient<SamplePayload>>,
): VersionHistoryClient<SamplePayload> {
  return {
    list: () =>
      Promise.resolve({
        data: [],
        total: 0,
        offset: 0,
        limit: 50,
        nextCursor: null,
        hasMore: false,
        pagination: {
          total: 0,
          offset: 0,
          limit: 50,
          next_cursor: null,
          has_more: false,
        },
      }),
    get: () => Promise.resolve({} as VersionSnapshot<SamplePayload>),
    diff: () => Promise.resolve(sampleDiff),
    rollback: () => Promise.resolve({} as VersionSnapshot<SamplePayload>),
    ...override,
  }
}

const meta = {
  title: 'VersionRollback/VersionDiffDrawer',
  component: VersionDiffDrawer<SamplePayload>,
  args: {
    client: makeClient(),
    fromVersion: 1,
    toVersion: 2,
    open: true,
    onClose: () => {},
  },
} satisfies Meta<typeof VersionDiffDrawer<SamplePayload>>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}

export const NoChanges: Story = {
  args: {
    client: makeClient({
      diff: () =>
        Promise.resolve({
          from_version: 1,
          to_version: 2,
          entries: [],
        }),
    }),
  },
}

export const ErrorState: Story = {
  args: {
    client: makeClient({
      diff: () => Promise.reject(new Error('Backend unreachable')),
    }),
  },
}
