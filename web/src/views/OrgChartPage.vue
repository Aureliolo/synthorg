<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { VueFlow, type Node, type Edge } from '@vue-flow/core'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'
import AppShell from '@/components/layout/AppShell.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import ErrorBoundary from '@/components/common/ErrorBoundary.vue'
import OrgNode from '@/components/org-chart/OrgNode.vue'
import { useCompanyStore } from '@/stores/company'
import { useAgentStore } from '@/stores/agents'
import { formatLabel } from '@/utils/format'
import { sanitizeForLog } from '@/utils/logging'

const router = useRouter()
const companyStore = useCompanyStore()
const agentStore = useAgentStore()

async function retryFetch() {
  try {
    await Promise.all([companyStore.fetchDepartments(), agentStore.fetchAgents()])
  } catch (err) {
    console.error('Org chart data fetch failed:', sanitizeForLog(err))
  }
}

onMounted(retryFetch)

const nodes = computed<Node[]>(() => {
  const result: Node[] = []
  let y = 0

  const agentIndex = new Map(agentStore.agents.map((a) => [a.name, a]))
  const addedAgents = new Set<string>()

  for (const dept of companyStore.departments) {
    const deptId = `dept-${dept.name}`
    result.push({
      id: deptId,
      position: { x: 0, y },
      data: { label: formatLabel(dept.name), type: 'department' },
      type: 'orgNode',
    })
    y += 120

    for (const team of dept.teams) {
      const teamId = `team-${dept.name}-${team.name}`
      result.push({
        id: teamId,
        position: { x: 50, y },
        data: { label: team.name, type: 'team' },
        type: 'orgNode',
      })
      y += 100
      for (let i = 0; i < team.members.length; i++) {
        const memberName = team.members[i] // eslint-disable-line security/detect-object-injection
        if (addedAgents.has(memberName)) continue
        addedAgents.add(memberName)
        const agent = agentIndex.get(memberName)
        result.push({
          id: `agent-${memberName}`,
          position: { x: 100 + i * 200, y },
          data: {
            label: memberName,
            type: 'agent',
            status: agent?.status,
            role: agent?.role,
            level: agent?.level,
          },
          type: 'orgNode',
        })
      }
      y += 120
    }
  }

  return result
})

const edges = computed<Edge[]>(() => {
  const result: Edge[] = []

  for (const dept of companyStore.departments) {
    const deptId = `dept-${dept.name}`
    for (const team of dept.teams) {
      const teamId = `team-${dept.name}-${team.name}`
      result.push({
        id: `${deptId}-${teamId}`,
        source: deptId,
        target: teamId,
        animated: true,
      })
      for (const member of team.members) {
        result.push({
          id: `${teamId}-agent-${member}`,
          source: teamId,
          target: `agent-${member}`,
        })
      }
    }
  }

  return result
})

function onNodeClick(event: { node: Node }) {
  if (event.node.id.startsWith('agent-')) {
    const name = event.node.id.replace('agent-', '')
    router.push(`/agents/${encodeURIComponent(name)}`)
  }
}
</script>

<template>
  <AppShell>
    <PageHeader title="Organization Chart" subtitle="Visual structure of departments, teams, and agents" />

    <ErrorBoundary :error="companyStore.departmentsError ?? agentStore.error" @retry="retryFetch">
      <LoadingSkeleton v-if="companyStore.departmentsLoading || agentStore.loading" :lines="6" />
      <div v-else class="h-[calc(100vh-200px)] rounded-lg border border-slate-800 bg-slate-900">
        <VueFlow
          :nodes="nodes"
          :edges="edges"
          fit-view-on-init
          @node-click="onNodeClick"
        >
          <template #node-orgNode="{ data }">
            <OrgNode :data="data" />
          </template>
          <Controls />
          <MiniMap />
        </VueFlow>
      </div>
    </ErrorBoundary>
  </AppShell>
</template>
