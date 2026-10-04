<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { type Chunk, type Document, documents, errorMessage } from '../api'
import AppHeader from '../components/AppHeader.vue'

const props = defineProps<{ id: number }>()
const router = useRouter()
const document = ref<Document>()
const chunks = ref<Chunk[]>([])
const error = ref('')

function location(chunk: Chunk) {
  if (chunk.page !== null) return `Page ${chunk.page}`
  return chunk.section ?? `Passage ${chunk.position + 1}`
}

onMounted(async () => {
  try {
    document.value = await documents.get(props.id)
    if (document.value.status !== 'ready') {
      await router.replace({ name: 'processing', params: { id: props.id } })
      return
    }
    chunks.value = await documents.chunks(props.id)
  } catch (e) {
    error.value = errorMessage(e)
  }
})
</script>

<template>
  <AppHeader />
  <main class="page">
    <RouterLink to="/" class="muted">← Library</RouterLink>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <template v-if="document">
      <h1>{{ document.title }}</h1>
      <p class="muted">
        {{ document.token_count?.toLocaleString() }} tokens · {{ chunks.length }} passages
      </p>
      <p class="notice">The concept graph for this document arrives in the next step.</p>

      <h2>Source passages</h2>
      <ol class="passages">
        <li v-for="chunk in chunks" :key="chunk.id">
          <div class="passage-location">{{ location(chunk) }}</div>
          <p>{{ chunk.text }}</p>
        </li>
      </ol>
    </template>
  </main>
</template>
