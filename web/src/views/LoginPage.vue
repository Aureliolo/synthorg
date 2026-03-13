<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import { useToast } from 'primevue/usetoast'
import { useAuthStore } from '@/stores/auth'
import { getErrorMessage } from '@/utils/errors'
import { MIN_PASSWORD_LENGTH } from '@/utils/constants'
import { useLoginLockout } from '@/composables/useLoginLockout'

const router = useRouter()
const auth = useAuthStore()
const toast = useToast()
const { locked, checkAndClearLockout, recordFailure, reset } = useLoginLockout()

const username = ref('')
const password = ref('')
const error = ref<string | null>(null)

async function handleLogin() {
  if (checkAndClearLockout()) {
    error.value = 'Too many failed attempts. Please wait before trying again.'
    return
  }
  error.value = null
  try {
    await auth.login(username.value, password.value)
    reset()
    if (auth.mustChangePassword) {
      toast.add({ severity: 'warn', summary: 'Password change required', life: 5000 })
    }
    router.push('/')
  } catch (err) {
    const lockoutMsg = recordFailure(err)
    if (lockoutMsg) {
      error.value = lockoutMsg
      return
    }
    error.value = getErrorMessage(err)
  }
}

function goToSetup() {
  router.push('/setup')
}
</script>

<template>
  <div class="flex min-h-screen items-center justify-center bg-slate-950 p-4">
    <div class="w-full max-w-sm">
      <!-- Logo -->
      <div class="mb-8 text-center">
        <div class="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-brand-600 text-xl font-bold text-white">
          S
        </div>
        <h1 class="text-2xl font-semibold text-slate-100">SynthOrg</h1>
        <p class="mt-1 text-sm text-slate-400">Sign in to your dashboard</p>
      </div>

      <!-- Form -->
      <form class="space-y-4" @submit.prevent="handleLogin">
        <div>
          <label for="username" class="mb-1 block text-sm text-slate-300">Username</label>
          <InputText
            id="username"
            v-model="username"
            class="w-full"
            placeholder="Enter username"
            autocomplete="username"
          />
        </div>
        <div>
          <label for="password" class="mb-1 block text-sm text-slate-300">Password</label>
          <InputText
            id="password"
            v-model="password"
            type="password"
            class="w-full"
            :placeholder="`Password (min ${MIN_PASSWORD_LENGTH} chars)`"
            autocomplete="current-password"
          />
        </div>

        <div v-if="error" role="alert" class="rounded bg-red-500/10 p-3 text-sm text-red-400">
          {{ error }}
        </div>

        <Button
          type="submit"
          label="Sign In"
          icon="pi pi-sign-in"
          class="w-full"
          :loading="auth.loading"
          :disabled="!username || !password || locked"
        />
      </form>

      <div class="mt-6 text-center">
        <button
          class="text-sm text-slate-500 hover:text-brand-400"
          @click="goToSetup"
        >
          First time? Set up admin account
        </button>
      </div>
    </div>
  </div>
</template>
