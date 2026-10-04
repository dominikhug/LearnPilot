<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Concept } from '../api'
import { MASTERY_THRESHOLD, stateIcon, stateLabel } from '../concepts'

const props = defineProps<{ concept: Concept | null; conceptsById: Map<number, Concept> }>()
const emit = defineEmits<{ learn: [id: number, startLocked: boolean]; cancel: [] }>()
const dialog = ref<HTMLDialogElement>()

const missing = computed(() =>
  (props.concept?.prerequisite_ids ?? [])
    .flatMap((id) => props.conceptsById.get(id) ?? [])
    .filter((p) => p.state !== 'mastered'),
)
const learnFirst = computed(() =>
  props.concept?.learn_first_id == null
    ? undefined
    : props.conceptsById.get(props.concept.learn_first_id),
)

// A native modal <dialog> brings focus trapping, Escape and the backdrop for free.
// Opened after rendering, so the primary button is there to take the focus.
watch(
  () => props.concept,
  (concept) => (concept ? dialog.value?.showModal() : dialog.value?.close()),
  { flush: 'post' },
)
</script>

<template>
  <dialog
    ref="dialog"
    class="dialog"
    aria-labelledby="locked-title"
    @cancel.prevent="emit('cancel')"
  >
    <template v-if="concept">
      <h2 id="locked-title">
        <span aria-hidden="true">{{ stateIcon.locked }}</span> {{ concept.name }} is locked
      </h2>
      <p>These prerequisites are not mastered yet:</p>
      <ul class="plain-list locked-prerequisites">
        <li v-for="p in missing" :key="p.id">
          <span aria-hidden="true">{{ stateIcon[p.state] }}</span>
          <span class="visually-hidden">{{ stateLabel[p.state] }}: </span>
          <span class="locked-prerequisite-name">{{ p.name }}</span>
          <span class="muted">
            <span class="visually-hidden">Mastery </span>{{ p.mastery.toFixed(2) }} /
            {{ MASTERY_THRESHOLD.toFixed(2) }}
          </span>
        </li>
      </ul>
      <p>Learning {{ concept.name }} now may be harder.</p>
      <div class="actions stacked">
        <button v-if="learnFirst" autofocus @click="emit('learn', learnFirst.id, false)">
          Learn {{ learnFirst.name }} first
        </button>
        <button class="secondary" @click="emit('learn', concept.id, true)">
          Start {{ concept.name }} anyway
        </button>
        <button class="secondary" @click="emit('cancel')">Cancel</button>
      </div>
    </template>
  </dialog>
</template>
