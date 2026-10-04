<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { type Document, type Limits, documentRoute, documents, errorMessage } from '../api'
import AppHeader from '../components/AppHeader.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'

const router = useRouter()
const items = ref<Document[]>([])
const limits = ref<Limits>()
const loading = ref(true)
const loadError = ref('')
const uploadError = ref('')
const uploading = ref(false)
const fileInput = ref<HTMLInputElement>()
const toDelete = ref<Document | null>(null)
const deleting = ref(false)
const deleteError = ref('')

const statusLabel: Record<Document['status'], string> = {
  processing: 'Processing',
  ready: 'Ready',
  failed: 'Failed',
}

async function load() {
  loadError.value = ''
  try {
    ;[items.value, limits.value] = await Promise.all([documents.list(), documents.limits()])
  } catch (e) {
    loadError.value = errorMessage(e)
  } finally {
    loading.value = false
  }
}

function checkFile(file: File): string {
  if (!limits.value) return ''
  const suffix = file.name.includes('.') ? file.name.slice(file.name.lastIndexOf('.')).toLowerCase() : ''
  if (!limits.value.file_types.includes(suffix)) {
    return 'Unsupported file type. Upload a PDF, Markdown or text file.'
  }
  if (file.size > limits.value.max_upload_mb * 1024 * 1024) {
    return `The file is larger than ${limits.value.max_upload_mb} MB.`
  }
  return ''
}

async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = '' // so choosing the same file again fires another change event
  if (!file) return
  uploadError.value = checkFile(file)
  if (uploadError.value) return

  uploading.value = true
  try {
    const document = await documents.upload(file)
    await router.push(documentRoute(document))
  } catch (e) {
    uploadError.value = errorMessage(e)
  } finally {
    uploading.value = false
  }
}

function askDelete(document: Document) {
  deleteError.value = ''
  toDelete.value = document
}

async function confirmDelete() {
  if (!toDelete.value) return
  deleting.value = true
  try {
    await documents.remove(toDelete.value.id)
    items.value = items.value.filter((d) => d.id !== toDelete.value?.id)
    toDelete.value = null
  } catch (e) {
    deleteError.value = errorMessage(e)
  } finally {
    deleting.value = false
  }
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { dateStyle: 'medium' })
}

onMounted(load)
</script>

<template>
  <AppHeader />
  <main class="page">
    <div class="page-head">
      <h1>Library</h1>
      <button :disabled="uploading || !limits" @click="fileInput?.click()">
        {{ uploading ? 'Reading document…' : 'Upload document' }}
      </button>
      <input
        ref="fileInput"
        type="file"
        :accept="limits?.file_types.join(',')"
        hidden
        @change="upload"
      />
    </div>
    <p v-if="limits" class="muted hint">
      PDF, Markdown or text · up to {{ limits.max_upload_mb }} MB and
      {{ limits.max_document_tokens.toLocaleString() }} tokens
    </p>
    <p v-if="uploadError" class="error" role="alert">{{ uploadError }}</p>

    <p v-if="loading" class="muted">Loading…</p>
    <p v-else-if="loadError" class="error" role="alert">
      {{ loadError }} <button class="secondary" @click="load">Try again</button>
    </p>
    <p v-else-if="items.length === 0" class="empty muted">
      No documents yet. Upload one to get started.
    </p>

    <ul v-else class="doc-list">
      <li class="doc-row doc-row-head" aria-hidden="true">
        <span>Title</span><span>Language</span><span>Status</span><span>Uploaded</span><span />
      </li>
      <li v-for="document in items" :key="document.id" class="doc-row">
        <RouterLink :to="documentRoute(document)" class="doc-title">{{ document.title }}</RouterLink>
        <span class="doc-meta">
          <span class="label">Language</span>{{ document.language?.toUpperCase() ?? '—' }}
        </span>
        <span class="doc-meta">
          <span class="label">Status</span>
          <span class="badge" :class="document.status">{{ statusLabel[document.status] }}</span>
        </span>
        <span class="doc-meta">
          <span class="label">Uploaded</span>{{ formatDate(document.created_at) }}
        </span>
        <button class="secondary small" @click="askDelete(document)">Delete</button>
      </li>
    </ul>
  </main>

  <ConfirmDialog
    :open="toDelete !== null"
    :title="`Delete “${toDelete?.title}”?`"
    confirm-label="Delete"
    danger
    :busy="deleting"
    @confirm="confirmDelete"
    @cancel="toDelete = null"
  >
    <p>The document and all learning progress for it are deleted. This cannot be undone.</p>
    <p v-if="deleteError" class="error" role="alert">{{ deleteError }}</p>
  </ConfirmDialog>
</template>
