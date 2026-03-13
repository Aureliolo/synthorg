<script setup lang="ts">
defineProps<{
  title: string
  value: string | number
  icon: string
  color?: string
  subtitle?: string
}>()
</script>

<template>
  <div class="rounded-lg border border-slate-800 bg-slate-900 p-5">
    <div class="flex items-start justify-between">
      <div>
        <p class="text-sm text-slate-400">{{ title }}</p>
        <p class="mt-1 text-2xl font-semibold text-slate-100">{{ value }}</p>
        <p v-if="subtitle" class="mt-1 text-xs text-slate-500">{{ subtitle }}</p>
      </div>
      <div
        :class="[
          'flex h-10 w-10 items-center justify-center rounded-lg',
          color ?? 'bg-brand-600/10 text-brand-400',
        ]"
      >
        <i :class="[icon, 'text-lg']" aria-hidden="true" />
      </div>
    </div>
  </div>
</template>
