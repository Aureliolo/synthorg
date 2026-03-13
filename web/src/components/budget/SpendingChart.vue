<script setup lang="ts">
import { computed } from 'vue'
import VChart from 'vue-echarts'
import type { CostRecord } from '@/api/types'
import { colors } from '@/styles/theme'

const props = defineProps<{
  records: CostRecord[]
}>()

const chartOption = computed(() => {
  const dailyData = new Map<string, number>()
  for (const record of props.records) {
    const date = new Date(record.timestamp)
    const dayKey = `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}-${String(date.getUTCDate()).padStart(2, '0')}`
    dailyData.set(dayKey, (dailyData.get(dayKey) ?? 0) + record.cost_usd)
  }

  const entries = Array.from(dailyData.entries()).sort(([a], [b]) => a.localeCompare(b))

  return {
    tooltip: {
      trigger: 'axis',
      formatter: (params: Array<{ name: string; value: number }>) => {
        if (!params.length) return ''
        const p = params[0]
        return `${p.name}<br/>$${p.value.toFixed(4)}`
      },
    },
    grid: { left: 60, right: 20, top: 20, bottom: 40 },
    xAxis: {
      type: 'category',
      data: entries.map(([k]) => k),
      axisLabel: { color: colors.surface[400], fontSize: 10, rotate: 45 },
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
        type: 'bar',
        data: entries.map(([, v]) => v),
        itemStyle: { color: colors.brand[500], borderRadius: [4, 4, 0, 0] },
      },
    ],
  }
})
</script>

<template>
  <div>
    <VChart
      v-if="records.length > 0"
      :option="chartOption"
      :style="{ height: '300px', width: '100%' }"
      autoresize
    />
    <div v-else class="flex h-[300px] items-center justify-center text-sm text-slate-500">
      No spending data available
    </div>
  </div>
</template>
