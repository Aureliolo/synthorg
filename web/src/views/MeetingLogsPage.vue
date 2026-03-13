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
      message="Meeting logs will be available when the meeting protocol backend is fully implemented."
    >
      <template #action>
        <a
          href="https://github.com/Aureliolo/synthorg/issues/264"
          target="_blank"
          rel="noopener noreferrer"
          class="text-sm text-brand-400 hover:text-brand-300"
        >
          Track progress on GitHub (#264)
        </a>
      </template>
    </EmptyState>
  </AppShell>
</template>
