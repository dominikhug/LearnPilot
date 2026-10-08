<script setup lang="ts">
import { computed } from 'vue'
import type { Chunk, Concept } from '../api'
import {
  MASTERY_THRESHOLD,
  chunkLocation,
  keyIdeaIcon,
  keyIdeaLabel,
  stateIcon,
  stateLabel,
} from '../concepts'

const props = defineProps<{
  concept: Concept
  conceptsById: Map<number, Concept>
  chunksById: Map<number, Chunk>
  /** Desktop only: editing touch screens is error-prone. */
  editable?: boolean
}>()
const emit = defineEmits<{ close: []; select: [id: number]; learn: [id: number]; edit: [] }>()

const learnLabel = { unlocked: 'Start learning', in_progress: 'Continue learning', mastered: 'Review' }

const prerequisites = computed(() =>
  props.concept.prerequisite_ids.flatMap((id) => props.conceptsById.get(id) ?? []),
)
const untested = computed(() => props.concept.key_idea_count - props.concept.tested_key_ideas.length)
const sources = computed(() =>
  props.concept.source_chunk_ids.flatMap((id) => props.chunksById.get(id) ?? []),
)
</script>

<template>
  <section class="concept-details" :aria-label="`Details: ${concept.name}`">
    <div class="concept-details-head">
      <button v-if="editable" class="secondary small" @click="emit('edit')">Edit</button>
      <button class="secondary small" @click="emit('close')">Close</button>
    </div>
    <h2>{{ concept.name }}</h2>
    <p class="concept-state" :class="concept.state">
      <span aria-hidden="true">{{ stateIcon[concept.state] }}</span> {{ stateLabel[concept.state] }}
    </p>
    <p>{{ concept.definition }}</p>
    <div class="actions start">
      <button v-if="concept.state !== 'locked'" @click="emit('learn', concept.id)">
        {{ learnLabel[concept.state] }}
      </button>
      <template v-else>
        <p class="muted">Locked until its prerequisites are mastered.</p>
        <button class="secondary" @click="emit('learn', concept.id)">Start learning…</button>
      </template>
    </div>

    <h3>Mastery</h3>
    <div class="mastery">
      <meter :value="concept.mastery" min="0" max="1" :optimum="1" :low="MASTERY_THRESHOLD" />
      <span>{{ concept.mastery.toFixed(2) }} / {{ MASTERY_THRESHOLD.toFixed(2) }}</span>
    </div>

    <h3>Prerequisites</h3>
    <ul v-if="prerequisites.length" class="plain-list">
      <li v-for="p in prerequisites" :key="p.id">
        <button class="link" @click="emit('select', p.id)">
          <span aria-hidden="true">{{ stateIcon[p.state] }}</span> {{ p.name }}
        </button>
      </li>
    </ul>
    <p v-else class="muted">None – a good place to start.</p>

    <h3>Key ideas</h3>
    <ul v-if="concept.tested_key_ideas.length" class="plain-list key-idea-status">
      <li v-for="k in concept.tested_key_ideas" :key="k.id">
        <span aria-hidden="true">{{ keyIdeaIcon[k.status] }}</span>
        <span class="visually-hidden">{{ keyIdeaLabel[k.status] }}: </span>
        {{ k.text }}
      </li>
    </ul>
    <p v-if="untested" class="muted">
      {{ untested }} {{ concept.tested_key_ideas.length ? 'more ' : '' }}key
      {{ untested === 1 ? 'idea' : 'ideas' }}, not yet tested. They appear here once a question
      has tested them.
    </p>

    <h3>Source passages</h3>
    <ol class="passages">
      <li v-for="chunk in sources" :key="chunk.id">
        <div class="passage-location">{{ chunkLocation(chunk) }}</div>
        <p>{{ chunk.text }}</p>
      </li>
    </ol>
  </section>
</template>
