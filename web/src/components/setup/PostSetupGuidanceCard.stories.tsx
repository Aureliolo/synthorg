import type { Meta, StoryObj } from '@storybook/react-vite'
import { MemoryRouter } from 'react-router'
import { PostSetupGuidanceCard } from './PostSetupGuidanceCard'

const meta = {
  title: 'Setup/PostSetupGuidanceCard',
  component: PostSetupGuidanceCard,
  args: {
    onDismiss: () => {},
  },
  decorators: [
    (Story) => (
      <MemoryRouter>
        <Story />
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof PostSetupGuidanceCard>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}
