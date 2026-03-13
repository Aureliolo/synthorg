<script setup lang="ts">
import { ref, watch, toRef } from 'vue'
import Button from 'primevue/button'
import Textarea from 'primevue/textarea'
import { useConfirm } from 'primevue/useconfirm'
import { useAuth } from '@/composables/useAuth'
import type { ApprovalStatus } from '@/api/types'

const props = defineProps<{
  approvalId: string
  status: ApprovalStatus
  loading?: boolean
}>()

const { canWrite } = useAuth()

const emit = defineEmits<{
  approve: [id: string, comment: string]
  reject: [id: string, reason: string]
}>()

const confirm = useConfirm()
const comment = ref('')
const rejectReason = ref('')
const showReject = ref(false)

watch(toRef(props, 'approvalId'), () => {
  comment.value = ''
  rejectReason.value = ''
  showReject.value = false
})

function handleApprove(id: string) {
  confirm.require({
    message: 'Are you sure you want to approve this request?',
    header: 'Confirm Approval',
    icon: 'pi pi-check-circle',
    accept: () => {
      emit('approve', id, comment.value)
      comment.value = ''
    },
  })
}

function handleReject(id: string) {
  if (!rejectReason.value.trim()) return
  emit('reject', id, rejectReason.value)
  rejectReason.value = ''
  showReject.value = false
}
</script>

<template>
  <div v-if="status === 'pending' && canWrite" class="space-y-3">
    <div>
      <label for="approval-comment" class="mb-1 block text-xs text-slate-400">Comment (optional)</label>
      <Textarea id="approval-comment" v-model="comment" class="w-full" rows="2" placeholder="Add a comment..." />
    </div>

    <div v-if="!showReject" class="flex gap-2">
      <Button
        label="Approve"
        icon="pi pi-check"
        severity="success"
        size="small"
        :disabled="loading"
        :loading="loading"
        @click="handleApprove(approvalId)"
      />
      <Button
        label="Reject"
        icon="pi pi-times"
        severity="danger"
        outlined
        size="small"
        :disabled="loading"
        @click="showReject = true"
      />
    </div>

    <div v-else class="space-y-2">
      <label for="reject-reason" class="mb-1 block text-xs text-slate-400">Reason for rejection</label>
      <Textarea id="reject-reason" v-model="rejectReason" class="w-full" rows="2" placeholder="Reason for rejection (required)" aria-required="true" />
      <div class="flex gap-2">
        <Button
          label="Confirm Reject"
          icon="pi pi-times"
          severity="danger"
          size="small"
          :disabled="!rejectReason.trim() || loading"
          :loading="loading"
          @click="handleReject(approvalId)"
        />
        <Button label="Back" text size="small" @click="showReject = false" />
      </div>
    </div>
  </div>
</template>
