<script setup lang="ts">
import { ref, onMounted } from 'vue'
import TabView from 'primevue/tabview'
import TabPanel from 'primevue/tabpanel'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import { useToast } from 'primevue/usetoast'
import AppShell from '@/components/layout/AppShell.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import { useAuthStore } from '@/stores/auth'
import { useCompanyStore } from '@/stores/company'
import { useProviderStore } from '@/stores/providers'
import { getErrorMessage } from '@/utils/errors'
import { MIN_PASSWORD_LENGTH } from '@/utils/constants'

const toast = useToast()
const auth = useAuthStore()
const companyStore = useCompanyStore()
const providerStore = useProviderStore()
const loading = ref(true)

const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const pwdError = ref<string | null>(null)

onMounted(async () => {
  try {
    await Promise.all([companyStore.fetchConfig(), providerStore.fetchProviders()])
  } finally {
    loading.value = false
  }
})

async function handleChangePassword() {
  pwdError.value = null
  if (newPassword.value !== confirmPassword.value) {
    pwdError.value = 'Passwords do not match'
    return
  }
  if (newPassword.value.length < MIN_PASSWORD_LENGTH) {
    pwdError.value = `Password must be at least ${MIN_PASSWORD_LENGTH} characters`
    return
  }
  try {
    await auth.changePassword(currentPassword.value, newPassword.value)
    toast.add({ severity: 'success', summary: 'Password changed', life: 3000 })
    currentPassword.value = ''
    newPassword.value = ''
    confirmPassword.value = ''
  } catch (err) {
    pwdError.value = getErrorMessage(err)
  }
}
</script>

<template>
  <AppShell>
    <PageHeader title="Settings" subtitle="Manage your dashboard configuration" />

    <LoadingSkeleton v-if="loading" :lines="6" />
    <TabView v-else value="company">
      <!-- Company Config -->
      <TabPanel header="Company" value="company">
        <div v-if="companyStore.config" class="space-y-4">
          <div class="rounded-lg border border-slate-800 p-4">
            <h4 class="mb-3 text-sm font-medium text-slate-300">Company Name</h4>
            <p class="text-lg text-slate-200">{{ companyStore.config.company_name }}</p>
          </div>
          <div class="rounded-lg border border-slate-800 p-4">
            <h4 class="mb-3 text-sm font-medium text-slate-300">Agents ({{ companyStore.config.agents.length }})</h4>
            <div class="flex flex-wrap gap-2">
              <span
                v-for="agent in companyStore.config.agents"
                :key="agent.name"
                class="rounded bg-slate-800 px-2 py-1 text-xs text-slate-300"
              >
                {{ agent.name }} ({{ agent.role }})
              </span>
            </div>
          </div>
        </div>
      </TabPanel>

      <!-- Providers -->
      <TabPanel header="Providers" value="providers">
        <DataTable :value="Object.values(providerStore.providers)" striped-rows class="text-sm">
          <Column field="name" header="Provider" sortable />
          <Column field="driver" header="Driver" />
          <Column field="enabled" header="Enabled" style="width: 80px">
            <template #body="{ data }">
              <span :class="data.enabled ? 'text-green-400' : 'text-red-400'">
                {{ data.enabled ? 'Yes' : 'No' }}
              </span>
            </template>
          </Column>
          <Column header="Models" style="width: 200px">
            <template #body="{ data }">
              <span class="text-xs text-slate-400">
                {{ data.models?.map((m: Record<string, string>) => m.name).join(', ') }}
              </span>
            </template>
          </Column>
        </DataTable>
      </TabPanel>

      <!-- User Settings -->
      <TabPanel header="User" value="user">
        <div class="max-w-md space-y-4">
          <div class="rounded-lg border border-slate-800 p-4">
            <h4 class="mb-3 text-sm font-medium text-slate-300">Account Info</h4>
            <div class="space-y-2 text-sm">
              <div class="flex justify-between">
                <span class="text-slate-400">Username</span>
                <span class="text-slate-200">{{ auth.user?.username }}</span>
              </div>
              <div class="flex justify-between">
                <span class="text-slate-400">Role</span>
                <span class="text-slate-200">{{ auth.user?.role }}</span>
              </div>
            </div>
          </div>

          <div class="rounded-lg border border-slate-800 p-4">
            <h4 class="mb-3 text-sm font-medium text-slate-300">Change Password</h4>
            <form class="space-y-3" @submit.prevent="handleChangePassword">
              <InputText v-model="currentPassword" type="password" class="w-full" placeholder="Current password" />
              <InputText v-model="newPassword" type="password" class="w-full" :placeholder="`New password (min ${MIN_PASSWORD_LENGTH} chars)`" />
              <InputText v-model="confirmPassword" type="password" class="w-full" placeholder="Confirm new password" />
              <div v-if="pwdError" class="rounded bg-red-500/10 p-2 text-sm text-red-400">{{ pwdError }}</div>
              <Button
                type="submit"
                label="Change Password"
                size="small"
                :loading="auth.loading"
                :disabled="!currentPassword || !newPassword || !confirmPassword"
              />
            </form>
          </div>
        </div>
      </TabPanel>
    </TabView>
  </AppShell>
</template>
