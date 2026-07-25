import type { Meta, StoryObj } from '@storybook/react'
import { MemoryRouter } from 'react-router'
import LoginPage from './LoginPage'
import {
  setupStatusComplete,
  setupStatusNeedsAdmin,
  authLoginSuccess,
  authSetupSuccess,
} from '@/mocks/handlers'

const meta = {
  title: 'Pages/Login',
  component: LoginPage,
  parameters: { layout: 'fullscreen' },
  decorators: [
    (Story) => (
      <MemoryRouter>
        <Story />
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof LoginPage>

export default meta
type Story = StoryObj<typeof meta>

/** Default login form. API returns setup-complete status. */
export const DefaultLogin: Story = {
  beforeEach({ msw }) {
    msw.use(...setupStatusComplete, ...authLoginSuccess)
  }
}

/** Admin creation form. API returns needs-admin status. */
export const AdminCreation: Story = {
  beforeEach({ msw }) {
    msw.use(...setupStatusNeedsAdmin, ...authSetupSuccess)
  }
}
