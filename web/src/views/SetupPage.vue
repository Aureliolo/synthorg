<script setup lang="ts">
import { ref } from 'vue'
import { useRouter, RouterLink } from 'vue-router'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import { useAuthStore } from '@/stores/auth'
import { getErrorMessage } from '@/utils/errors'
import { MIN_PASSWORD_LENGTH } from '@/utils/constants'
import { useLoginLockout } from '@/composables/useLoginLockout'

const router = useRouter()
const auth = useAuthStore()
const { locked, checkAndClearLockout, recordFailure } = useLoginLockout()

const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const error = ref<string | null>(null)

async function handleSetup() {
  if (checkAndClearLockout()) {
    error.value = 'Too many failed attempts. Please wait before trying again.'
    return
  }
  error.value = null
  if (password.value !== confirmPassword.value) {
    error.value = 'Passwords do not match'
    return
  }
  if (password.value.length < MIN_PASSWORD_LENGTH) {
    error.value = `Password must be at least ${MIN_PASSWORD_LENGTH} characters`
    return
  }
  try {
    await auth.setup(username.value, password.value)
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
</script>

<template>
  <div class="flex min-h-screen items-center justify-center bg-slate-950 p-4">
    <div class="w-full max-w-sm">
      <div class="mb-8 text-center">
        <div class="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-brand-600 text-xl font-bold text-white">
          S
        </div>
        <h1 class="text-2xl font-semibold text-slate-100">Initial Setup</h1>
        <p class="mt-1 text-sm text-slate-400">Create the first admin (CEO) account</p>
      </div>

      <form class="space-y-4" @submit.prevent="handleSetup">
        <div>
          <label for="username" class="mb-1 block text-sm text-slate-300">Username</label>
          <InputText
            id="username"
            v-model="username"
            class="w-full"
            placeholder="Admin username"
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
            :placeholder="`Min ${MIN_PASSWORD_LENGTH} characters`"
            autocomplete="new-password"
          />
        </div>
        <div>
          <label for="confirm" class="mb-1 block text-sm text-slate-300">Confirm Password</label>
          <InputText
            id="confirm"
            v-model="confirmPassword"
            type="password"
            class="w-full"
            placeholder="Re-enter password"
            autocomplete="new-password"
          />
        </div>

        <div v-if="error" role="alert" class="rounded bg-red-500/10 p-3 text-sm text-red-400">
          {{ error }}
        </div>

        <Button
          type="submit"
          label="Create Admin Account"
          icon="pi pi-check"
          class="w-full"
          :loading="auth.loading"
          :disabled="!username || !password || !confirmPassword || locked"
        />
      </form>

      <div class="mt-6 text-center">
        <RouterLink
          to="/login"
          class="text-sm text-slate-500 hover:text-brand-400"
        >
          Already have an account? Sign in
        </RouterLink>
      </div>
    </div>
  </div>
</template>
