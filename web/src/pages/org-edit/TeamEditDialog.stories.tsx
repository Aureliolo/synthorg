import type { Meta, StoryObj } from '@storybook/react-vite'
import { TeamEditDialog } from './TeamEditDialog'
import type { TeamConfig } from '@/api/types/org'

const sampleTeam: TeamConfig = {
  name: 'platform',
  lead: 'alice',
  members: ['bob', 'carla'],
}

const meta = {
  title: 'OrgEdit/TeamEditDialog',
  component: TeamEditDialog,
  parameters: {
    a11y: { test: 'error' },
  },
  args: {
    open: true,
    onOpenChange: () => {},
    onCreateTeam: async () => sampleTeam,
    onUpdateTeam: async () => sampleTeam,
  },
} satisfies Meta<typeof TeamEditDialog>

export default meta
type Story = StoryObj<typeof meta>

export const Create: Story = {
  args: { mode: 'create' },
}

export const Edit: Story = {
  args: { mode: 'edit', team: sampleTeam },
}

export const Closed: Story = {
  args: { mode: 'create', open: false },
}
