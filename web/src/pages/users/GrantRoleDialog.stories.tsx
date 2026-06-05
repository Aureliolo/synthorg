import type { Meta, StoryObj } from '@storybook/react-vite'
import { GrantRoleDialog } from './GrantRoleDialog'
import { useUsersStore } from '@/stores/users'
import type { UserResponse } from '@/api/endpoints/users'

const sampleUser: UserResponse = {
  id: 'user-1',
  username: 'alice@example.com',
  role: 'observer',
  must_change_password: false,
  org_roles: ['viewer'],
  scoped_departments: [],
  created_at: '2026-04-28T08:00:00+00:00',
  updated_at: '2026-04-28T08:00:00+00:00',
}

const meta = {
  title: 'Users/GrantRoleDialog',
  component: GrantRoleDialog,
  args: {
    open: true,
    onClose: () => {},
  },
  decorators: [
    (Story) => {
      useUsersStore.setState({
        submitting: false,
        grantOrgRole: () => Promise.resolve(sampleUser),
      })
      return <Story />
    },
  ],
} satisfies Meta<typeof GrantRoleDialog>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = { args: { user: sampleUser } }
export const Closed: Story = { args: { user: sampleUser, open: false } }
