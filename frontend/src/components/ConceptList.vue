<script setup lang="ts">
import { computed } from 'vue'
import type { Concept } from '../api'
import { stateIcon, stateLabel } from '../concepts'

const props = defineProps<{ concepts: Concept[]; conceptsById: Map<number, Concept> }>()
const emit = defineEmits<{ select: [id: number] }>()

// Grouped by level so the list keeps the learning order.
const levels = computed(() => {
  const groups = new Map<number, Concept[]>()
  for (const concept of props.concepts) {
    groups.set(concept.level, [...(groups.get(concept.level) ?? []), concept])
  }
  return [...groups.entries()].sort(([a], [b]) => a - b)
})

function waitingFor(concept: Concept) {
  if (concept.state !== 'locked') return []
  return concept.prerequisite_ids
    .flatMap((id) => props.conceptsById.get(id) ?? [])
    .filter((p) => p.state !== 'mastered')
    .map((p) => p.name)
}
</script>

<template>
  <div class="concept-list">
    <section v-for="[level, group] in levels" :key="level">
      <h2>Level {{ level }}</h2>
      <ul class="plain-list">
        <li v-for="concept in group" :key="concept.id">
          <button class="concept-list-item" :class="concept.state" @click="emit('select', concept.id)">
            <span aria-hidden="true">{{ stateIcon[concept.state] }}</span>
            <span class="visually-hidden">{{ stateLabel[concept.state] }}: </span>
            <span class="concept-list-name">{{ concept.name }}</span>
            <span v-if="concept.state === 'in_progress'" class="muted">
              {{ concept.mastery.toFixed(2) }}
            </span>
            <span v-if="waitingFor(concept).length" class="concept-list-needs muted">
              needs: {{ waitingFor(concept).join(', ') }}
            </span>
          </button>
        </li>
      </ul>
    </section>
  </div>
</template>
