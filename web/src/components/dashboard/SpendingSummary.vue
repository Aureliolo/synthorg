<script setup lang="ts">
import { computed } from 'vue'
import VChart from 'vue-echarts'
import type { CostRecord } from '@/api/types'
import { colors } from '@/styles/theme'

const props = defineProps<{
  records: CostRecord[]
  totalCost: number
}>()

const chartOption = computed(() => {
  // Sort records by timestamp descending, then group by hour for the spending chart
  const sorted = [...props.records].sort((a, b) =>
    new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime(),
  )
  const hourlyData = new Map<string, number>()
  for (const record of sorted) {
    const date = new Date(record.timestamp)
    const hourKey = `${date.getUTCFullYear()}-${date.getUTCMonth() + 1}/${date.getUTCDate()} ${date.getUTCHours()}:00`
    hourlyData.set(hourKey, (hourlyData.get(hourKey) ?? 0) + record.cost_usd)
  }

  // hourlyData is in descending order (newest first from sorted input).
  // Take first 24 entries (most recent), then reverse for chronological display.
  const entries = Array.from(hourlyData.entries()).slice(0, 24).reverse()

  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 50, right: 20, top: 10, bottom: 30 },
    xAxis: {
      type: 'category',
      data: entries.map(([k]) => k),
      axisLabel: { color: colors.surface[400], fontSize: 10 },
      axisLine: { lineStyle: { color: colors.surface[200] } },
    },
    yAxis: {
      type: 'value',
      axisLabel: {
        color: colors.surface[400],
        fontSize: 10,
        formatter: (v: number) => `$${v.toFixed(2)}`,
      },
      splitLine: { lineStyle: { color: colors.surface[100] } },
    },
    series: [
      {
        type: 'line',
        data: entries.map(([, v]) => v),
        smooth: true,
        areaStyle: { color: `${colors.brand[500]}20` },
        lineStyle: { color: colors.brand[500] },
        itemStyle: { color: colors.brand[500] },
      },
    ],
  }
})
</script>

<template>
  <div class="rounded-lg border border-slate-800 bg-slate-900 p-5">
    <div class="mb-4 flex items-center justify-between">
      <h3 class="text-sm font-medium text-slate-300">Spending</h3>
      <span class="text-lg font-semibold text-slate-100">${{ totalCost.toFixed(4) }}</span>
    </div>
    <VChart
      v-if="records.length > 0"
      :option="chartOption"
      :style="{ height: '200px', width: '100%' }"
      autoresize
    />
    <div v-else class="flex h-[200px] items-center justify-center text-sm text-slate-500">
      No spending data yet
    </div>
  </div>
</template>
