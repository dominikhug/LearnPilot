<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { type Chunk, type ConceptGraph, type Document, documents, errorMessage } from '../api'
import ConceptDetails from '../components/ConceptDetails.vue'
import ConceptList from '../components/ConceptList.vue'
import GraphCanvas from '../components/GraphCanvas.vue'
import AppHeader from '../components/AppHeader.vue'
import { stateIcon, stateLabel } from '../concepts'

const props = defineProps<{ id: number }>()
const router = useRouter()
const document = ref<Document>()
const graph = ref<ConceptGraph>()
const chunks = ref<Chunk[]>([])
const error = ref('')
const selectedId = ref<number | null>(null)

// One breakpoint: phones get the list by level instead of the graph.
const phoneQuery = window.matchMedia('(max-width: 768px)')
const isPhone = ref(phoneQuery.matches)
const onBreakpoint = (e: MediaQueryListEvent) => (isPhone.value = e.matches)

const conceptsById = computed(() => new Map(graph.value?.concepts.map((c) => [c.id, c])))
const chunksById = computed(() => new Map(chunks.value.map((c) => [c.id, c])))
const selected = computed(() =>
  selectedId.value === null ? undefined : conceptsById.value.get(selectedId.value),
)
const legendStates = ['locked', 'unlocked', 'in_progress', 'mastered'] as const

onMounted(async () => {
  phoneQuery.addEventListener('change', onBreakpoint)
  try {
    document.value = await documents.get(props.id)
    if (document.value.status !== 'ready') {
      await router.replace({ name: 'processing', params: { id: props.id } })
      return
    }
    ;[graph.value, chunks.value] = await Promise.all([
      documents.graph(props.id),
      documents.chunks(props.id),
    ])
  } catch (e) {
    error.value = errorMessage(e)
  }
})
function learn(conceptId: number) {
  router.push({ name: 'learn', params: { id: conceptId } })
}

onUnmounted(() => phoneQuery.removeEventListener('change', onBreakpoint))
</script>

<template>
  <AppHeader />
  <main class="page hub">
    <RouterLink to="/" class="muted">← Library</RouterLink>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <template v-if="document && graph">
      <h1>{{ document.title }}</h1>
      <p class="muted">
        {{ graph.concepts.length }} concepts · {{ document.language?.toUpperCase() ?? '—' }} ·
        {{ document.token_count?.toLocaleString() }} tokens
      </p>
      <ul class="legend plain-list" aria-label="Legend">
        <li v-for="state in legendStates" :key="state">
          <span aria-hidden="true">{{ stateIcon[state] }}</span> {{ stateLabel[state] }}
        </li>
      </ul>

      <template v-if="isPhone">
        <ConceptList
          :concepts="graph.concepts"
          :concepts-by-id="conceptsById"
          @select="selectedId = $event"
        />
        <div v-if="selected" class="details-fullscreen">
          <ConceptDetails
            :concept="selected"
            :concepts-by-id="conceptsById"
            :chunks-by-id="chunksById"
            @close="selectedId = null"
            @select="selectedId = $event"
            @learn="learn"
          />
        </div>
      </template>

      <div v-else class="hub-graph" :class="{ 'with-panel': selected }">
        <GraphCanvas :graph="graph" :selected-id="selectedId" @select="selectedId = $event" />
        <ConceptDetails
          v-if="selected"
          class="side-panel"
          :concept="selected"
          :concepts-by-id="conceptsById"
          :chunks-by-id="chunksById"
          @close="selectedId = null"
          @select="selectedId = $event"
          @learn="learn"
        />
      </div>
    </template>
  </main>
</template>
