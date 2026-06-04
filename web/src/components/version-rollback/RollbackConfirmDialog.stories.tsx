import type { Meta, StoryObj } from '@storybook/react-vite'
import { RollbackConfirmDialog } from './RollbackConfirmDialog'
import type {
  VersionHistoryClient,
  VersionSnapshot,
} from '@/api/endpoints/version-history'

type SamplePayload = Record<string, unknown>

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
    diff: () =>
      Promise.resolve({
        from_version: 1,
        to_version: 2,
        entries: [],
      }),
    rollback: () => Promise.resolve({} as VersionSnapshot<SamplePayload>),
    ...override,
  }
}

const meta = {
  title: 'VersionRollback/RollbackConfirmDialog',
  component: RollbackConfirmDialog<SamplePayload>,
  args: {
    client: makeClient(),
    toVersion: 2,
    open: true,
    onClose: () => {},
  },
} satisfies Meta<typeof RollbackConfirmDialog<SamplePayload>>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}
export const Closed: Story = { args: { open: false } }
