<!--
  ErrorBoundary — error display wrapper (not a Vue error-capture boundary).
  Expects an `error` prop from the parent; when set it renders an error message
  with a retry button. When `error` is falsy it renders the default slot.
  Does NOT use onErrorCaptured — child component errors must be caught externally.
-->
<script setup lang="ts">
import Button from 'primevue/button'

defineProps<{
  error?: string | null
}>()

defineEmits<{
  retry: []
}>()
</script>

<template>
  <div v-if="error" role="alert" class="rounded-lg border border-red-500/20 bg-red-500/5 p-6 text-center">
    <i class="pi pi-exclamation-triangle mb-3 text-3xl text-red-400" aria-hidden="true" />
    <p class="text-sm text-red-300">{{ error }}</p>
    <Button
      label="Retry"
      icon="pi pi-refresh"
      severity="danger"
      text
      size="small"
      class="mt-3"
      @click="$emit('retry')"
    />
  </div>
  <slot v-else />
</template>
