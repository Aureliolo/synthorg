<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import Button from 'primevue/button'
import Menu from 'primevue/menu'
import { useAuthStore } from '@/stores/auth'
import { useWebSocketStore } from '@/stores/websocket'
import ConnectionStatus from './ConnectionStatus.vue'

defineEmits<{
  toggleSidebar: []
}>()

const router = useRouter()
const auth = useAuthStore()
const wsStore = useWebSocketStore()
const userMenu = ref()
const menuItems = ref([
  {
    label: 'Settings',
    icon: 'pi pi-cog',
    command: () => router.push('/settings'),
  },
  { separator: true },
  {
    label: 'Logout',
    icon: 'pi pi-sign-out',
    command: () => {
      try {
        wsStore.disconnect()
      } catch {
        // Ensure logout completes even if WS disconnect fails
      }
      auth.logout()
    },
  },
])

function toggleUserMenu(event: Event) {
  userMenu.value.toggle(event)
}
</script>

<template>
  <header class="flex h-14 items-center justify-between border-b border-slate-800 bg-slate-950 px-4">
    <div class="flex items-center gap-3">
      <Button
        icon="pi pi-bars"
        text
        severity="secondary"
        class="lg:hidden"
        aria-label="Toggle sidebar"
        @click="$emit('toggleSidebar')"
      />
    </div>

    <div class="flex items-center gap-4">
      <ConnectionStatus />

      <Button
        :label="auth.user?.username ?? ''"
        icon="pi pi-user"
        text
        severity="secondary"
        class="text-slate-300"
        :aria-label="auth.user?.username ?? 'Open user menu'"
        @click="toggleUserMenu"
      />
      <Menu ref="userMenu" :model="menuItems" :popup="true" />
    </div>
  </header>
</template>
