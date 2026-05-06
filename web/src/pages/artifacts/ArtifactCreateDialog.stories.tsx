import type { Meta, StoryObj } from '@storybook/react-vite'
import { ArtifactCreateDialog } from './ArtifactCreateDialog'
import type { Artifact } from '@/api/types/artifacts'

const stubArtifact: Artifact = {
  id: 'art-1',
  type: 'code',
  path: 'src/example/file.py',
  task_id: 'task-1',
  created_by: 'alice',
  description: '',
  project_id: null,
  content_type: 'text/x-python',
  size_bytes: 0,
  created_at: '2026-04-28T08:00:00+00:00',
}

const meta = {
  title: 'Artifacts/ArtifactCreateDialog',
  component: ArtifactCreateDialog,
  parameters: {
    a11y: { test: 'error' },
  },
  args: {
    open: true,
    onOpenChange: () => {},
    onCreate: async () => stubArtifact,
  },
} satisfies Meta<typeof ArtifactCreateDialog>

export default meta
type Story = StoryObj<typeof meta>

export const Open: Story = {}

export const Closed: Story = {
  args: { open: false },
}
