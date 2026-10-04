<script setup lang="ts">
import { ref, watch } from 'vue'

const props = defineProps<{
  open: boolean
  title: string
  confirmLabel: string
  danger?: boolean
  busy?: boolean
}>()
const emit = defineEmits<{ confirm: []; cancel: [] }>()
const dialog = ref<HTMLDialogElement>()

// A native modal <dialog> brings focus trapping, Escape and the backdrop for free.
watch(
  () => props.open,
  (open) => (open ? dialog.value?.showModal() : dialog.value?.close()),
)
</script>

<template>
  <dialog ref="dialog" class="dialog" @cancel.prevent="emit('cancel')">
    <h2>{{ title }}</h2>
    <slot />
    <div class="actions">
      <button class="secondary" :disabled="busy" @click="emit('cancel')">Cancel</button>
      <button :class="{ danger }" :disabled="busy" autofocus @click="emit('confirm')">
        {{ confirmLabel }}
      </button>
    </div>
  </dialog>
</template>
