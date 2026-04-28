import type { Meta, StoryObj } from '@storybook/react-vite'
import { MemoryRouter } from 'react-router'
import UsersPage from './UsersPage'
import { useUsersStore } from '@/stores/users'
import type { UserResponse } from '@/api/endpoints/users'

const sampleUsers: readonly UserResponse[] = [
  {
    id: 'user-1',
    username: 'alice@example.com',
    role: 'observer',
    must_change_password: false,
    org_roles: ['viewer'],
    scoped_departments: [],
    created_at: '2026-04-20T08:00:00+00:00',
    updated_at: '2026-04-25T08:00:00+00:00',
  },
  {
    id: 'user-2',
    username: 'bob@example.com',
    role: 'observer',
    must_change_password: false,
    org_roles: ['editor'],
    scoped_departments: ['engineering'],
    created_at: '2026-04-21T08:00:00+00:00',
    updated_at: '2026-04-25T08:00:00+00:00',
  },
]

const meta = {
  title: 'Pages/UsersPage',
  component: UsersPage,
  parameters: { layout: 'fullscreen' },
  decorators: [
    (Story) => {
      useUsersStore.setState({
        users: sampleUsers,
        loading: false,
        loadingMore: false,
        error: null,
        hasMore: false,
        submitting: false,
      })
      return (
        <MemoryRouter>
          <Story />
        </MemoryRouter>
      )
    },
  ],
} satisfies Meta<typeof UsersPage>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}

export const Loading: Story = {
  decorators: [
    (Story) => {
      useUsersStore.setState({
        users: [],
        loading: true,
        loadingMore: false,
        error: null,
        hasMore: false,
        submitting: false,
      })
      return (
        <MemoryRouter>
          <Story />
        </MemoryRouter>
      )
    },
  ],
}

export const Empty: Story = {
  decorators: [
    (Story) => {
      useUsersStore.setState({
        users: [],
        loading: false,
        loadingMore: false,
        error: null,
        hasMore: false,
        submitting: false,
      })
      return (
        <MemoryRouter>
          <Story />
        </MemoryRouter>
      )
    },
  ],
}

export const ErrorState: Story = {
  decorators: [
    (Story) => {
      useUsersStore.setState({
        users: [],
        loading: false,
        loadingMore: false,
        error: 'Backend unreachable',
        hasMore: false,
        submitting: false,
      })
      return (
        <MemoryRouter>
          <Story />
        </MemoryRouter>
      )
    },
  ],
}
