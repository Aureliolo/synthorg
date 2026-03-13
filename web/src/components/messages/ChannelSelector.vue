<script setup lang="ts">
import Dropdown from 'primevue/dropdown'
import type { Channel } from '@/api/types'

defineProps<{
  channels: Channel[]
  modelValue: string | null
}>()

defineEmits<{
  'update:modelValue': [value: string | null]
}>()
</script>

<template>
  <Dropdown
    :model-value="modelValue"
    :options="channels"
    option-label="name"
    option-value="name"
    placeholder="All Channels"
    show-clear
    class="w-48"
    aria-label="Filter by channel"
    @update:model-value="$emit('update:modelValue', $event)"
  />
</template>
