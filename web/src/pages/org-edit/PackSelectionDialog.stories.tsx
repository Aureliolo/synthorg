import type { Meta, StoryObj } from '@storybook/react'
import { http, HttpResponse } from 'msw'
import { PackSelectionDialog } from './PackSelectionDialog'
import type { PackInfoResponse } from '@/api/types/templates'

const mockPacks: readonly PackInfoResponse[] = [
  {
    name: 'security-team',
    display_name: 'Security Team',
    description: 'A focused security team with Security Engineer and Security Operations.',
    source: 'builtin',
    tags: ['security', 'compliance'],
    agent_count: 2,
    department_count: 1,
  },
  {
    name: 'data-team',
    display_name: 'Data Team',
    description: 'Data Analyst, Data Engineer, and ML Engineer for analytics pipelines.',
    source: 'builtin',
    tags: ['data', 'analytics'],
    agent_count: 3,
    department_count: 1,
  },
  {
    name: 'custom-pack',
    display_name: 'Custom Pack',
    description: 'A user-defined template pack.',
    source: 'user',
    tags: ['custom'],
    agent_count: 1,
    department_count: 1,
  },
]

const meta = {
  title: 'OrgEdit/PackSelectionDialog',
  component: PackSelectionDialog,

  beforeEach({ msw }) {
    msw.use(http.get('*/template-packs', () =>
      HttpResponse.json({ success: true, data: mockPacks, error: null, error_detail: null }),
    ), http.post('*/template-packs/apply', () =>
      HttpResponse.json({
        success: true,
        data: { pack_name: 'security-team', agents_added: 2, departments_added: 1 },
        error: null,
        error_detail: null,
      }),
    ))
  },

  parameters: {
    a11y: { test: 'error' }
  },

  args: {
    open: true,
    onOpenChange: () => {},
  }
} satisfies Meta<typeof PackSelectionDialog>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}

export const Closed: Story = {
  args: { open: false },
}

export const Empty: Story = {
  beforeEach({ msw }) {
    msw.use(http.get('*/template-packs', () =>
      HttpResponse.json({ success: true, data: [], error: null, error_detail: null }),
    ))
  }
}

export const Loading: Story = {
  beforeEach({ msw }) {
    msw.use(http.get('*/template-packs', async () => {
      await new Promise(() => {})
      return HttpResponse.json({ success: true, data: [], error: null, error_detail: null })
    }))
  }
}

export const Error: Story = {
  beforeEach({ msw }) {
    msw.use(http.get('*/template-packs', () =>
      HttpResponse.json(
        { success: false, data: null, error: 'Failed to load packs', error_detail: null },
        { status: 500 },
      ),
    ))
  }
}

export const Disabled: Story = {
  args: { disabled: true },
}
