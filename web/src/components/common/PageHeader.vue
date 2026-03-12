<script setup lang="ts">
defineProps<{
  title: string
  subtitle?: string
}>()
</script>

<template>
  <div class="mb-6">
    <h1 class="text-2xl font-semibold text-slate-100">{{ title }}</h1>
    <p v-if="subtitle" class="mt-1 text-sm text-slate-400">{{ subtitle }}</p>
    <div v-if="$slots.actions" class="mt-3 flex gap-2">
      <slot name="actions" />
    </div>
  </div>
</template>
