<script setup lang="ts">
import AppShell from '@/components/layout/AppShell.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import EmptyState from '@/components/common/EmptyState.vue'
</script>

<template>
  <AppShell>
    <PageHeader title="Meeting Logs" subtitle="View agent meeting transcripts and outcomes" />
    <EmptyState
      icon="pi pi-video"
      title="Coming Soon"
      message="Meeting logs will be available when the meeting protocol is fully implemented."
    />
  </AppShell>
</template>
