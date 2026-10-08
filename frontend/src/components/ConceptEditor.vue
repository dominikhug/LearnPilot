<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { type Concept, type KeyIdea, editing, errorMessage } from '../api'
import { keyIdeaIcon, keyIdeaLabel, stateIcon } from '../concepts'
import ConfirmDialog from './ConfirmDialog.vue'

const props = defineProps<{ concept: Concept; conceptsById: Map<number, Concept> }>()
// `changed`: the graph needs reloading. `deleted`: the concept is gone.
const emit = defineEmits<{ done: []; changed: []; deleted: [] }>()

const name = ref(props.concept.name)
const keyIdeas = ref<KeyIdea[]>([])
const newPrerequisite = ref<number | null>(null)
// The key idea whose text is being edited, and the edited text.
const editingId = ref<number | null>(null)
const draft = ref('')
const error = ref('')
const busy = ref(false)
// The deletion waiting for confirmation.
const pending = ref<{ keyIdea: KeyIdea } | { concept: Concept } | null>(null)

const prerequisites = computed(() =>
  props.concept.prerequisite_ids.flatMap((id) => props.conceptsById.get(id) ?? []),
)

/** Concepts building on this one, directly or not; as prerequisites they would close a cycle. */
const dependents = computed(() => {
  const all = [...props.conceptsById.values()]
  const result = new Set<number>()
  const queue = [props.concept.id]
  while (queue.length) {
    const id = queue.pop()!
    for (const c of all) {
      if (c.prerequisite_ids.includes(id) && !result.has(c.id)) {
        result.add(c.id)
        queue.push(c.id)
      }
    }
  }
  return result
})

const candidates = computed(() =>
  [...props.conceptsById.values()]
    .filter(
      (c) =>
        c.id !== props.concept.id &&
        !props.concept.prerequisite_ids.includes(c.id) &&
        !dependents.value.has(c.id),
    )
    .sort((a, b) => a.name.localeCompare(b.name)),
)

onMounted(() => run(async () => (keyIdeas.value = await editing.keyIdeas(props.concept.id))))

/** Runs one change at a time; a failure shows its message and leaves the form as it was. */
async function run(action: () => Promise<unknown>): Promise<boolean> {
  busy.value = true
  error.value = ''
  try {
    await action()
    return true
  } catch (e) {
    error.value = errorMessage(e)
    return false
  } finally {
    busy.value = false
  }
}

async function rename() {
  if (name.value.trim() === props.concept.name) return
  if (await run(() => editing.rename(props.concept.id, name.value))) emit('changed')
}

async function addPrerequisite() {
  const id = newPrerequisite.value
  if (id === null) return
  if (await run(() => editing.addPrerequisite(props.concept.id, id))) {
    newPrerequisite.value = null
    emit('changed')
  }
}

async function removePrerequisite(id: number) {
  if (await run(() => editing.removePrerequisite(props.concept.id, id))) emit('changed')
}

function startEdit(keyIdea: KeyIdea) {
  editingId.value = keyIdea.id
  draft.value = keyIdea.text
}

async function saveKeyIdea(keyIdea: KeyIdea) {
  const ok = await run(async () => {
    const saved = await editing.editKeyIdea(keyIdea.id, draft.value)
    keyIdeas.value = keyIdeas.value.map((k) => (k.id === keyIdea.id ? saved : k))
  })
  if (ok) {
    editingId.value = null
    emit('changed')
  }
}

async function confirmDelete() {
  const target = pending.value
  if (target === null) return
  if ('concept' in target) {
    if (await run(() => editing.removeConcept(target.concept.id))) emit('deleted')
  } else {
    const id = target.keyIdea.id
    if (await run(() => editing.removeKeyIdea(id))) {
      keyIdeas.value = keyIdeas.value.filter((k) => k.id !== id)
      emit('changed')
    }
  }
  pending.value = null
}
</script>

<template>
  <section class="concept-details concept-editor" :aria-label="`Edit: ${concept.name}`">
    <div class="concept-details-head">
      <button class="small" @click="emit('done')">Done</button>
    </div>
    <h2>Edit concept</h2>
    <p v-if="error" class="error" role="alert">{{ error }}</p>

    <form class="editor-row" @submit.prevent="rename">
      <label class="visually-hidden" for="concept-name">Name</label>
      <input id="concept-name" v-model="name" maxlength="200" required />
      <button class="small" :disabled="busy || name.trim() === concept.name">Rename</button>
    </form>

    <h3>Prerequisites</h3>
    <ul v-if="prerequisites.length" class="plain-list editor-list">
      <li v-for="p in prerequisites" :key="p.id">
        <span><span aria-hidden="true">{{ stateIcon[p.state] }}</span> {{ p.name }}</span>
        <button
          class="secondary small"
          :disabled="busy"
          :aria-label="`Remove prerequisite ${p.name}`"
          @click="removePrerequisite(p.id)"
        >
          Remove
        </button>
      </li>
    </ul>
    <p v-else class="muted">None.</p>
    <form v-if="candidates.length" class="editor-row" @submit.prevent="addPrerequisite">
      <label class="visually-hidden" for="new-prerequisite">Add a prerequisite</label>
      <select id="new-prerequisite" v-model="newPrerequisite">
        <option :value="null" disabled>Add a prerequisite…</option>
        <option v-for="c in candidates" :key="c.id" :value="c.id">{{ c.name }}</option>
      </select>
      <button class="small" :disabled="busy || newPrerequisite === null">Add</button>
    </form>
    <p class="hint muted">Concepts that build on this one are not offered: that would be a loop.</p>

    <h3>Key ideas</h3>
    <p class="hint muted">
      All key ideas are shown here, including untested ones: they are what answers are graded
      against. Changing a key idea's text resets it to not tested.
    </p>
    <ul class="plain-list editor-list">
      <li v-for="k in keyIdeas" :key="k.id">
        <form v-if="editingId === k.id" class="key-idea-form" @submit.prevent="saveKeyIdea(k)">
          <label class="visually-hidden" :for="`key-idea-${k.id}`">Key idea</label>
          <textarea :id="`key-idea-${k.id}`" v-model="draft" rows="3" maxlength="1000" required />
          <div class="actions start">
            <button class="small" :disabled="busy || !draft.trim()">Save</button>
            <button type="button" class="secondary small" @click="editingId = null">Cancel</button>
          </div>
        </form>
        <template v-else>
          <span>
            <span aria-hidden="true">{{ keyIdeaIcon[k.status] }}</span>
            <span class="visually-hidden">{{ keyIdeaLabel[k.status] }}: </span>
            {{ k.text }}
          </span>
          <span class="editor-buttons">
            <button class="secondary small" :disabled="busy" @click="startEdit(k)">Edit</button>
            <button
              class="secondary small"
              :disabled="busy || keyIdeas.length === 1"
              :title="keyIdeas.length === 1 ? 'A concept needs at least one key idea' : undefined"
              @click="pending = { keyIdea: k }"
            >
              Delete
            </button>
          </span>
        </template>
      </li>
    </ul>

    <h3>Delete concept</h3>
    <p class="muted">
      Removes the concept with its key ideas, questions, answers and learning progress.
    </p>
    <div class="actions start">
      <button class="danger" :disabled="busy" @click="pending = { concept }">
        Delete concept…
      </button>
    </div>

    <ConfirmDialog
      :open="pending !== null"
      :title="
        pending && 'concept' in pending ? `Delete “${concept.name}”?` : 'Delete this key idea?'
      "
      confirm-label="Delete"
      danger
      :busy="busy"
      @confirm="confirmDelete"
      @cancel="pending = null"
    >
      <template v-if="pending && 'concept' in pending">
        <p>
          Its key ideas, questions, answers and learning progress are deleted, and so are its links
          to other concepts. Concepts that build on it may become unlocked. This cannot be undone.
        </p>
      </template>
      <template v-else-if="pending">
        <p>“{{ pending.keyIdea.text }}”</p>
        <p>Questions not answered yet stop testing it. This cannot be undone.</p>
      </template>
    </ConfirmDialog>
  </section>
</template>
