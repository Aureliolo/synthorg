import type { Meta, StoryObj } from '@storybook/react-vite'

import { SecurityConfigSection } from './SecurityConfigSection'

const meta = {
  title: 'Settings/SecurityConfigSection',
  component: SecurityConfigSection,
} satisfies Meta<typeof SecurityConfigSection>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}
