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
      message="Artifact browsing will be available when the artifact system backend is fully implemented."
    >
      <template #action>
        <a
          href="https://github.com/Aureliolo/synthorg/issues/233"
          target="_blank"
          rel="noopener noreferrer"
          class="text-sm text-brand-400 hover:text-brand-300"
        >
          Track progress on GitHub (#233)
        </a>
      </template>
    </EmptyState>
  </AppShell>
</template>
