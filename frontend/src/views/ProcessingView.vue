<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ApiError, type Document, documents, errorMessage } from '../api'
import AppHeader from '../components/AppHeader.vue'

const props = defineProps<{ id: number }>()
const router = useRouter()
const POLL_MS = 1000

const document = ref<Document>()
const error = ref('')
const notFound = ref(false)
const retrying = ref(false)
let timer: number | undefined

// Text extraction happens during the upload itself, so it is always done here.
const steps = [
  { key: 'extracting_text', label: 'Extracting text' },
  { key: 'counting_tokens', label: 'Counting tokens' },
] as const

type StepState = 'done' | 'active' | 'failed' | 'pending'

const stepStates = computed<StepState[]>(() => {
  const doc = document.value
  if (!doc) return steps.map(() => 'pending')
  const current = doc.step === 'done' ? steps.length : steps.findIndex((s) => s.key === doc.step)
  return steps.map((_, i) => {
    if (i < current || doc.status === 'ready') return 'done'
    if (i > current) return 'pending'
    return doc.status === 'failed' ? 'failed' : 'active'
  })
})

const stepIcon: Record<StepState, string> = { done: '✓', active: '…', failed: '✗', pending: '○' }
const stepText: Record<StepState, string> = {
  done: 'done',
  active: 'in progress',
  failed: 'failed',
  pending: 'waiting',
}

async function poll() {
  window.clearTimeout(timer)
  try {
    document.value = await documents.get(props.id)
    error.value = ''
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      notFound.value = true
      return
    }
    error.value = errorMessage(e) // a network hiccup: keep polling
  }
  if (document.value?.status === 'ready') {
    await router.replace({ name: 'document', params: { id: props.id } })
  } else if (document.value?.status !== 'failed') {
    timer = window.setTimeout(poll, POLL_MS)
  }
}

async function retry() {
  retrying.value = true
  try {
    document.value = await documents.retry(props.id)
    await poll()
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    retrying.value = false
  }
}

onMounted(poll)
onUnmounted(() => window.clearTimeout(timer))
</script>

<template>
  <AppHeader />
  <main class="centered-page">
    <div class="card status-card">
      <template v-if="notFound">
        <h1>Document not found</h1>
        <p class="muted">It may have been deleted.</p>
        <RouterLink to="/" class="button">Back to library</RouterLink>
      </template>

      <template v-else>
        <h1>{{ document?.title ?? 'Processing' }}</h1>
        <ol class="steps" aria-live="polite">
          <li v-for="(step, i) in steps" :key="step.key" :class="stepStates[i]">
            <span class="step-icon" aria-hidden="true">{{ stepIcon[stepStates[i]] }}</span>
            {{ step.label }}
            <span class="visually-hidden">: {{ stepText[stepStates[i]] }}</span>
          </li>
        </ol>

        <template v-if="document?.status === 'failed'">
          <p class="error" role="alert">{{ document.error_message }}</p>
          <div class="actions">
            <RouterLink to="/" class="button secondary">Back to library</RouterLink>
            <button v-if="document.can_retry" :disabled="retrying" @click="retry">Retry</button>
          </div>
        </template>
        <template v-else>
          <p v-if="error" class="error" role="alert">{{ error }}</p>
          <p class="muted">This page moves on by itself when the document is ready.</p>
          <RouterLink to="/" class="muted">Back to library</RouterLink>
        </template>
      </template>
    </div>
  </main>
</template>
