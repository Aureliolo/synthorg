<script setup lang="ts">
import AppShell from '@/components/layout/AppShell.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import EmptyState from '@/components/common/EmptyState.vue'
</script>

<template>
  <AppShell>
    <PageHeader title="Artifacts" subtitle="Browse files and outputs produced by agents" />
    <EmptyState
      icon="pi pi-file"
      title="Coming Soon"
      message="Artifact browsing will be available when the artifact system is fully implemented."
    />
  </AppShell>
</template>
