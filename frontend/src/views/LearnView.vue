<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  type Chunk,
  type Graded,
  type LearningSession,
  type Question,
  documents,
  errorMessage,
  learning,
} from '../api'
import AppHeader from '../components/AppHeader.vue'
import {
  MASTERY_THRESHOLD,
  chunkLocation,
  formatPoints,
  keyIdeaIcon,
  keyIdeaLabel,
} from '../concepts'

const props = defineProps<{ id: number }>()

const session = ref<LearningSession>()
const chunks = ref<Chunk[]>([])
const loadError = ref('')
const loading = ref(false)

// The question on screen and what happened to it. After grading, the question stays
// visible with its feedback until "Next question".
const question = ref<Question | null>(null)
const draft = ref('')
const submittedText = ref('')
const pendingAnswerId = ref<number | null>(null)
const result = ref<Graded>()
const busy = ref(false)
const actionError = ref('')

const disputing = ref(false)
const disputeReason = ref('')

const chunksById = computed(() => new Map(chunks.value.map((c) => [c.id, c])))
const sources = computed(() =>
  (result.value?.answer.source_chunk_ids ?? []).flatMap((id) => chunksById.value.get(id) ?? []),
)
const gradingFailed = computed(() => pendingAnswerId.value !== null && !busy.value)
const finished = computed(() => session.value && !question.value && !result.value)

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    show(await learning.start(props.id))
    chunks.value = await documents.chunks(session.value!.document_id)
  } catch (e) {
    loadError.value = errorMessage(e)
  } finally {
    loading.value = false
  }
}

function show(next: LearningSession) {
  session.value = next
  question.value = next.question
  result.value = undefined
  draft.value = ''
  const ungraded = next.ungraded_answer
  pendingAnswerId.value = ungraded?.id ?? null
  submittedText.value = ungraded?.text ?? ''
  actionError.value = ''
}

/** Returns whether grading succeeded. */
async function grade(call: () => Promise<Graded>) {
  busy.value = true
  actionError.value = ''
  try {
    const graded = await call()
    result.value = graded
    session.value = graded.session
    pendingAnswerId.value = null
    disputing.value = false
    return true
  } catch (e) {
    actionError.value = errorMessage(e)
    return false
  } finally {
    busy.value = false
  }
}

async function submit() {
  const text = draft.value.trim()
  if (!question.value || !text || busy.value) return
  submittedText.value = text
  const questionId = question.value.id
  if (await grade(() => learning.answer(questionId, text))) return
  // The answer is saved before grading; look it up so grading can be retried.
  try {
    const current = await learning.start(props.id)
    if (current.ungraded_answer?.question_id === questionId) {
      pendingAnswerId.value = current.ungraded_answer.id
    } else if (current.question?.id === questionId) {
      submittedText.value = '' // not saved: the draft is still there to submit again
    }
  } catch {
    // Keep the message from the failed attempt.
  }
}

function retryGrading() {
  const answerId = pendingAnswerId.value
  if (answerId !== null) return grade(() => learning.retryGrading(answerId))
}

function sendDispute() {
  const reason = disputeReason.value.trim()
  const answerId = result.value?.answer.id
  if (!reason || answerId === undefined) return
  return grade(() => learning.dispute(answerId, reason))
}

function next() {
  if (session.value) show(session.value)
  disputeReason.value = ''
  actionError.value = ''
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) submit()
}

onMounted(load)
</script>

<template>
  <AppHeader />
  <main class="page learn">
    <RouterLink
      :to="session ? { name: 'document', params: { id: session.document_id } } : '/'"
      class="muted"
    >
      ← Back to graph
    </RouterLink>

    <div v-if="loading && !session" class="learn-status" aria-live="polite">
      <p>Preparing questions…</p>
      <p class="muted">The first start of a concept takes a moment.</p>
    </div>
    <div v-else-if="loadError && !session" class="learn-status">
      <p class="error" role="alert">{{ loadError }}</p>
      <div class="actions">
        <button @click="load">Try again</button>
      </div>
    </div>

    <template v-if="session">
      <header class="learn-head">
        <h1>{{ session.concept_name }}</h1>
        <div class="mastery">
          <span>Mastery</span>
          <meter
            :value="session.mastery"
            min="0"
            max="1"
            :optimum="1"
            :low="MASTERY_THRESHOLD"
          />
          <span>{{ session.mastery.toFixed(2) }}</span>
        </div>
        <p class="muted">
          Key ideas correct: {{ session.key_ideas_correct }} / {{ session.key_idea_count }}
        </p>
      </header>

      <section v-if="question" class="learn-card" aria-label="Question">
        <div class="learn-card-head">
          <h2>Question {{ question.number }}</h2>
          <span class="muted">
            {{ question.points }} {{ question.points === 1 ? 'point' : 'points' }}
          </span>
        </div>
        <p class="question-text">{{ question.text }}</p>

        <template v-if="!submittedText">
          <label for="answer" class="visually-hidden">Your answer</label>
          <textarea
            id="answer"
            v-model="draft"
            rows="6"
            placeholder="Your answer"
            :disabled="busy"
            @keydown="onKeydown"
          />
          <div class="submit-bar">
            <button :disabled="busy || !draft.trim()" @click="submit">Submit</button>
          </div>
        </template>
        <template v-else>
          <h3>Your answer</h3>
          <p class="answer-text">{{ submittedText }}</p>
        </template>

        <p v-if="busy && !result" class="muted" aria-live="polite">Grading…</p>
        <div v-if="gradingFailed" class="learn-error">
          <p class="error" role="alert">
            Grading failed. {{ actionError }} Your answer is saved.
          </p>
          <button @click="retryGrading">Retry grading</button>
        </div>
        <p v-else-if="actionError && !result" class="error" role="alert">{{ actionError }}</p>
      </section>

      <section v-if="result" class="learn-card feedback" aria-label="Feedback" aria-live="polite">
        <div class="learn-card-head">
          <h2>Feedback</h2>
          <span>
            {{ formatPoints(result.answer.points_earned ?? 0) }} /
            {{ formatPoints(result.answer.points_possible) }} points
          </span>
        </div>
        <ul class="plain-list key-idea-feedback">
          <li v-for="k in result.answer.key_ideas" :key="k.id" :class="k.status">
            <span class="key-idea-icon" aria-hidden="true">{{ keyIdeaIcon[k.status] }}</span>
            <div>
              <strong>{{ k.text }}</strong>
              <span class="visually-hidden">: {{ keyIdeaLabel[k.status] }}</span>
              <p>{{ k.feedback }}</p>
            </div>
          </li>
        </ul>
        <p>
          Mastery {{ result.mastery_before.toFixed(2) }} → {{ result.session.mastery.toFixed(2) }}
        </p>
        <p v-if="result.answer.regraded" class="muted">
          Graded again with your reason: “{{ result.answer.dispute_reason }}”. This result is
          final.
        </p>

        <details v-if="sources.length" class="sources">
          <summary>Sources ({{ sources.map(chunkLocation).join(', ') }})</summary>
          <ol class="passages">
            <li v-for="chunk in sources" :key="chunk.id">
              <div class="passage-location">{{ chunkLocation(chunk) }}</div>
              <p>{{ chunk.text }}</p>
            </li>
          </ol>
        </details>

        <p v-if="busy" class="muted" aria-live="polite">Grading again…</p>
        <p v-if="actionError" class="error" role="alert">{{ actionError }}</p>

        <form v-if="disputing" class="dispute" @submit.prevent="sendDispute">
          <label for="dispute-reason">Why do you disagree?</label>
          <textarea
            id="dispute-reason"
            v-model="disputeReason"
            rows="3"
            maxlength="2000"
            :disabled="busy"
          />
          <p class="muted">Your answer is graded once more with this reason in mind.</p>
          <div class="actions">
            <button type="button" class="secondary" :disabled="busy" @click="disputing = false">
              Cancel
            </button>
            <button type="submit" :disabled="busy || !disputeReason.trim()">Grade again</button>
          </div>
        </form>

        <div v-else class="actions">
          <button
            v-if="result.answer.can_dispute"
            class="secondary"
            :disabled="busy"
            @click="disputing = true"
          >
            I disagree
          </button>
          <button :disabled="busy" @click="next">
            {{ result.session.question ? 'Next question' : 'Continue' }}
          </button>
        </div>
      </section>

      <section v-if="finished" class="learn-card" aria-live="polite">
        <template v-if="session.mastered">
          <h2>✅ {{ session.concept_name }} mastered</h2>
          <p>All key ideas answered correctly, with mastery {{ session.mastery.toFixed(2) }}.</p>
        </template>
        <template v-else>
          <h2>All planned questions answered</h2>
          <p class="muted">
            Mastery needs every key idea last answered correctly and a mastery above
            {{ MASTERY_THRESHOLD.toFixed(2) }}. Follow-up questions for the open key ideas are
            not available yet.
          </p>
        </template>
        <div class="actions">
          <RouterLink
            :to="{ name: 'document', params: { id: session.document_id } }"
            class="button"
          >
            Back to graph
          </RouterLink>
        </div>
      </section>
    </template>
  </main>
</template>
