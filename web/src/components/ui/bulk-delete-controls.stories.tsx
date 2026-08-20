import type { Meta, StoryObj } from '@storybook/react'
import { BulkDeleteControls } from './bulk-delete-controls'
import type { BulkSelection } from '@/hooks/use-bulk-selection'

function selection(overrides: Partial<BulkSelection> = {}): BulkSelection {
  return {
    visibleSelected: new Set(['a', 'b', 'c']),
    selectedCount: 3,
    toggle: () => undefined,
    clear: () => undefined,
    confirmOpen: false,
    openConfirm: () => undefined,
    closeConfirm: () => undefined,
    deleting: false,
    runDelete: () => Promise.resolve(),
    ...overrides,
  }
}

const meta = {
  title: 'UI/BulkDeleteControls',
  component: BulkDeleteControls,
  tags: ['autodocs'],
  parameters: {
    layout: 'fullscreen',
    a11y: { test: 'error' },
  },
  decorators: [
    (Story) => (
      <div className="flex h-64 flex-col justify-end">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof BulkDeleteControls>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: {
    selection: selection(),
    noun: { one: 'Project', many: 'projects' },
    description: 'This permanently removes the selected projects.',
    ariaLabel: 'Project bulk actions',
  },
}

// One row reads as one row: the count and the noun have to agree, or the
// confirmation says "Delete 1 projects" on the most careful click of all.
export const SingleRow: Story = {
  args: {
    ...Default.args,
    selection: selection({ visibleSelected: new Set(['a']), selectedCount: 1 }),
  },
}

export const Confirming: Story = {
  args: {
    ...Default.args,
    selection: selection({ confirmOpen: true }),
  },
}

export const Deleting: Story = {
  args: {
    ...Default.args,
    selection: selection({ confirmOpen: true, deleting: true }),
  },
}

// Nothing ticked: the bar is absent rather than empty.
export const Empty: Story = {
  args: {
    ...Default.args,
    selection: selection({ visibleSelected: new Set(), selectedCount: 0 }),
  },
}
