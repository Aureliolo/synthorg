<script setup lang="ts">
import Skeleton from 'primevue/skeleton'

defineProps<{
  lines?: number
}>()
</script>

<template>
  <div class="space-y-3">
    <Skeleton v-for="i in (lines ?? 3)" :key="i" height="1.5rem" class="bg-slate-800" />
  </div>
</template>
