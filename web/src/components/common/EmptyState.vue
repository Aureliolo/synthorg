<script setup lang="ts">
defineProps<{
  icon?: string
  title: string
  message?: string
}>()
</script>

<template>
  <div class="flex flex-col items-center justify-center py-16 text-center">
    <i
      v-if="icon"
      :class="[icon, 'mb-4 text-4xl text-slate-500']"
      aria-hidden="true"
    />
    <h3 class="text-lg font-medium text-slate-300">{{ title }}</h3>
    <p v-if="message" class="mt-2 max-w-md text-sm text-slate-500">{{ message }}</p>
    <div v-if="$slots.action" class="mt-4">
      <slot name="action" />
    </div>
  </div>
</template>
