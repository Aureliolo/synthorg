import type { Meta, StoryObj } from '@storybook/react-vite'
import { PostureBadge } from './PostureBadge'

const meta = {
  title: 'Setup/PostureBadge',
  component: PostureBadge,
  parameters: { layout: 'centered' },
} satisfies Meta<typeof PostureBadge>

export default meta
type Story = StoryObj<typeof meta>

export const Autonomous: Story = { args: { posture: 'autonomous' } }
export const SupervisedClientFacing: Story = {
  args: { posture: 'supervised_client_facing' },
}
export const KnowledgeHeavy: Story = { args: { posture: 'knowledge_heavy' } }
export const CostDisciplined: Story = { args: { posture: 'cost_disciplined' } }
export const SecurityHardened: Story = { args: { posture: 'security_hardened' } }
export const ResearchAutonomous: Story = { args: { posture: 'research_autonomous' } }
export const NoPosture: Story = { args: { posture: null } }
