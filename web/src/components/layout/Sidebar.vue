<script setup lang="ts">
import { useRouter, useRoute } from 'vue-router'
import { APP_NAME, NAV_ITEMS } from '@/utils/constants'

defineProps<{
  collapsed: boolean
}>()

defineEmits<{
  toggle: []
}>()

const router = useRouter()
const route = useRoute()

function isActive(to: string): boolean {
  if (to === '/') return route.path === '/'
  // Exact match or path segment match to avoid /agent matching /agents
  return route.path === to || route.path.startsWith(to + '/')
}

function navigate(to: string) {
  router.push(to)
}
</script>

<template>
  <aside
    :class="[
      'flex flex-col border-r border-slate-800 bg-slate-950 transition-all duration-200',
      collapsed ? 'w-16' : 'w-60',
    ]"
  >
    <!-- Logo -->
    <div class="flex h-14 items-center gap-2 border-b border-slate-800 px-4">
      <div class="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-sm font-bold text-white">
        S
      </div>
      <span v-if="!collapsed" class="text-lg font-semibold text-slate-100">{{ APP_NAME }}</span>
    </div>

    <!-- Navigation -->
    <nav class="flex-1 overflow-y-auto py-2">
      <button
        v-for="item in NAV_ITEMS"
        :key="item.to"
        type="button"
        :class="[
          'flex w-full items-center gap-3 px-4 py-2.5 text-sm transition-colors',
          isActive(item.to)
            ? 'bg-brand-600/10 text-brand-400'
            : 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200',
        ]"
        :title="collapsed ? item.label : undefined"
        :aria-label="item.label"
        @click="navigate(item.to)"
      >
        <i :class="[item.icon, 'text-base']" />
        <span v-if="!collapsed">{{ item.label }}</span>
      </button>
    </nav>

    <!-- Collapse toggle -->
    <button
      type="button"
      class="flex items-center justify-center border-t border-slate-800 py-3 text-slate-400 hover:text-slate-200"
      :aria-label="collapsed ? 'Expand sidebar' : 'Collapse sidebar'"
      @click="$emit('toggle')"
    >
      <i :class="collapsed ? 'pi pi-angle-right' : 'pi pi-angle-left'" aria-hidden="true" />
    </button>
  </aside>
</template>
