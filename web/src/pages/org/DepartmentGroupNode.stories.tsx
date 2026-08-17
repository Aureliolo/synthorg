import type { Meta, StoryObj } from '@storybook/react'
import { ReactFlow, ReactFlowProvider } from '@xyflow/react'
import { DepartmentGroupNode } from './DepartmentGroupNode'
import type { DepartmentGroupData } from './build-org-tree'
import { DEFAULT_CURRENCY } from '@/utils/currencies'

const nodeTypes = { department: DepartmentGroupNode }

function Wrapper({ data }: { data: DepartmentGroupData }) {
  return (
    <ReactFlowProvider>
      <div style={{ width: 500, height: 200 }}>
        <ReactFlow
          nodes={[
            {
              id: '1',
              type: 'department',
              position: { x: 20, y: 20 },
              data,
              style: { width: 400, height: 150 },
            },
          ]}
          edges={[]}
          nodeTypes={nodeTypes}
          fitView
          nodesDraggable={false}
          nodesConnectable={false}
          zoomOnScroll={false}
          panOnDrag={false}
        />
      </div>
    </ReactFlowProvider>
  )
}

const meta = {
  title: 'OrgChart/DepartmentGroupNode',
  component: Wrapper,
  tags: ['autodocs'],
  parameters: {
    a11y: { test: 'error' },
  },
} satisfies Meta<typeof Wrapper>

export default meta
type Story = StoryObj<typeof meta>

export const Populated: Story = {
  args: {
    data: {
      departmentName: 'engineering',
      displayName: 'Engineering',
      agentCount: 5,
      activeCount: 4,
      budgetPercent: 40,
      utilizationPercent: 60,
      cost7d: 45.8,
      currency: DEFAULT_CURRENCY,
      statusDots: [
        { agentId: 'a1', agentName: 'Anica Hocevar', runtimeStatus: 'active' },
        { agentId: 'a2', agentName: 'Daler Rumaysayev', runtimeStatus: 'active' },
        { agentId: 'a3', agentName: 'Feline Rek', runtimeStatus: 'active' },
        { agentId: 'a4', agentName: 'Toni Roce', runtimeStatus: 'idle' },
        { agentId: 'a5', agentName: 'Mira Halvorsen', runtimeStatus: 'idle' },
      ],
      isEmpty: false,
    },
  },
}

export const OverBudget: Story = {
  args: {
    data: {
      departmentName: 'product',
      displayName: 'Product',
      agentCount: 3,
      activeCount: 1,
      budgetPercent: 120,
      utilizationPercent: 95,
      cost7d: 22.3,
      currency: DEFAULT_CURRENCY,
      statusDots: [
        { agentId: 'p1', agentName: 'Daler Samandarov', runtimeStatus: 'active' },
        { agentId: 'p2', agentName: 'Qudret Amirova', runtimeStatus: 'error' },
        { agentId: 'p3', agentName: 'Rodrigo Sepulveda', runtimeStatus: 'idle' },
      ],
      isEmpty: false,
    },
  },
}

export const EmptyDepartment: Story = {
  args: {
    data: {
      departmentName: 'security',
      displayName: 'Security',
      agentCount: 0,
      activeCount: 0,
      budgetPercent: 8,
      utilizationPercent: null,
      cost7d: null,
      currency: null,
      statusDots: [],
      isEmpty: true,
    },
  },
}

export const DropTargetActive: Story = {
  args: {
    data: {
      departmentName: 'engineering',
      displayName: 'Engineering',
      agentCount: 5,
      activeCount: 3,
      budgetPercent: 40,
      utilizationPercent: 70,
      cost7d: 38.5,
      currency: DEFAULT_CURRENCY,
      statusDots: [
        { agentId: 'a1', agentName: 'Anica Hocevar', runtimeStatus: 'active' },
        { agentId: 'a2', agentName: 'Daler Rumaysayev', runtimeStatus: 'active' },
        { agentId: 'a3', agentName: 'Feline Rek', runtimeStatus: 'active' },
        { agentId: 'a4', agentName: 'Toni Roce', runtimeStatus: 'idle' },
        { agentId: 'a5', agentName: 'Mira Halvorsen', runtimeStatus: 'idle' },
      ],
      isEmpty: false,
      isDropTarget: true,
    },
  },
}
