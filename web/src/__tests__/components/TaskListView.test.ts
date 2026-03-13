import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import TaskListView from '@/components/tasks/TaskListView.vue'
import { DEFAULT_PAGE_SIZE } from '@/utils/constants'
import type { Task } from '@/api/types'

const DataTableStub = defineComponent({
  name: 'DataTable',
  props: ['value', 'totalRecords', 'loading', 'rows', 'paginator', 'stripedRows', 'rowHover'],
  emits: ['row-click', 'page'],
  template: '<table><slot /></table>',
})

const ColumnStub = defineComponent({
  name: 'PvColumn',
  props: ['field', 'header'],
  template: '<td />',
})

const StatusBadgeStub = defineComponent({
  name: 'StatusBadge',
  props: ['value', 'type'],
  template: '<span />',
})

const mockTask: Task = {
  id: 'task-1',
  title: 'Implement feature X',
  description: 'Build the feature',
  type: 'development',
  status: 'in_progress',
  priority: 'high',
  project: 'proj-1',
  created_by: 'user-1',
  assigned_to: 'agent-1',
  reviewers: [],
  dependencies: [],
  artifacts_expected: [],
  acceptance_criteria: [],
  estimated_complexity: 'medium',
  budget_limit: 10,
  deadline: null,
  max_retries: 3,
  parent_task_id: null,
  delegation_chain: [],
  task_structure: null,
  coordination_topology: 'auto',
}

function mountList(props: { tasks: Task[]; total: number; loading: boolean }) {
  return mount(TaskListView, {
    props,
    global: {
      stubs: {
        DataTable: DataTableStub,
        Column: ColumnStub,
        StatusBadge: StatusBadgeStub,
      },
    },
  })
}

describe('TaskListView', () => {
  it('passes DEFAULT_PAGE_SIZE as rows to DataTable', () => {
    const wrapper = mountList({ tasks: [], total: 0, loading: false })

    const table = wrapper.findComponent(DataTableStub)
    expect(table.props('rows')).toBe(DEFAULT_PAGE_SIZE)
  })

  it('renders with tasks prop', () => {
    const tasks = [mockTask]
    const wrapper = mountList({ tasks, total: 1, loading: false })

    const table = wrapper.findComponent(DataTableStub)
    expect(table.props('value')).toEqual(tasks)
    expect(table.props('totalRecords')).toBe(1)
  })

  it('passes loading state to DataTable', () => {
    const wrapper = mountList({ tasks: [], total: 0, loading: true })

    const table = wrapper.findComponent(DataTableStub)
    expect(table.props('loading')).toBe(true)
  })

  it('emits task-click on row click', async () => {
    const wrapper = mountList({ tasks: [mockTask], total: 1, loading: false })

    const table = wrapper.findComponent(DataTableStub)
    await table.vm.$emit('row-click', { data: mockTask })

    expect(wrapper.emitted('task-click')).toBeTruthy()
    expect(wrapper.emitted('task-click')![0]).toEqual([mockTask])
  })

  it('emits page event on pagination', async () => {
    const wrapper = mountList({ tasks: [mockTask], total: 100, loading: false })

    const pageEvent = { first: 50, rows: 50 }
    const table = wrapper.findComponent(DataTableStub)
    await table.vm.$emit('page', pageEvent)

    expect(wrapper.emitted('page')).toBeTruthy()
    expect(wrapper.emitted('page')![0]).toEqual([pageEvent])
  })
})
