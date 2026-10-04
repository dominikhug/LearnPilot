<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  type Chunk,
  type Explanation,
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
  angleLabel,
  chunkLocation,
  formatPoints,
  keyIdeaIcon,
  keyIdeaLabel,
  stateIcon,
  stateLabel,
} from '../concepts'

const props = defineProps<{ id: number }>()
const route = useRoute()

const session = ref<LearningSession>()
const chunks = ref<Chunk[]>([])
const loadError = ref('')
const loading = ref(false)

// The question on screen and what happened to it. After grading, the question stays
// visible with its feedback (and a re-explanation on a gap) until the learner moves on.
const question = ref<Question | null>(null)
const draft = ref('')
const submittedText = ref('')
const pendingAnswerId = ref<number | null>(null)
const result = ref<Graded>()
const busy = ref(false)
const actionError = ref('')

const disputing = ref(false)
const disputeReason = ref('')

const explanation = ref<Explanation>()
const explaining = ref(false)
const explainError = ref('')

// Moving on to the next question, which may have to be written first.
const advancing = ref(false)
const advanceError = ref('')
const showCompleted = ref(false)

const chunksById = computed(() => new Map(chunks.value.map((c) => [c.id, c])))
const passages = (ids: number[]) => ids.flatMap((id) => chunksById.value.get(id) ?? [])
const sources = computed(() => passages(result.value?.answer.source_chunk_ids ?? []))
const explanationSources = computed(() => passages(explanation.value?.source_chunk_ids ?? []))
const gradingFailed = computed(() => pendingAnswerId.value !== null && !busy.value)
const completed = computed(() => result.value?.completed ?? null)
const nothingToAsk = computed(
  () => session.value && !question.value && !result.value && !advancing.value,
)

async function load() {
  loading.value = true
  loadError.value = ''
  session.value = undefined
  showCompleted.value = false
  try {
    // Set when the learner confirmed starting a locked concept.
    show(await learning.start(props.id, route.query.start === 'locked'))
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
  explanation.value = undefined
  explainError.value = ''
  draft.value = ''
  const ungraded = next.ungraded_answer
  pendingAnswerId.value = ungraded?.id ?? null
  submittedText.value = ungraded?.text ?? ''
  actionError.value = ''
  disputeReason.value = ''
  disputing.value = false
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
    explanation.value = graded.answer.explanation ?? undefined
    if (graded.answer.needs_explanation && !explanation.value) explain()
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

async function explain() {
  const answerId = result.value?.answer.id
  if (answerId === undefined) return
  explaining.value = true
  explainError.value = ''
  try {
    explanation.value = await learning.explain(answerId)
  } catch (e) {
    explainError.value = errorMessage(e)
  } finally {
    explaining.value = false
  }
}

async function next() {
  if (completed.value) {
    showCompleted.value = true
    return
  }
  advancing.value = true
  advanceError.value = ''
  try {
    show(await learning.start(props.id))
  } catch (e) {
    advanceError.value = errorMessage(e)
  } finally {
    advancing.value = false
  }
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) submit()
}

onMounted(load)
// "Next concept" and the way out reuse this view for another concept.
watch(() => props.id, load)
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
      <div class="actions centered-actions">
        <RouterLink to="/" class="button secondary">Library</RouterLink>
        <button @click="load">Try again</button>
      </div>
    </div>

    <section
      v-if="session && completed && showCompleted"
      class="learn-card learn-complete"
      aria-live="polite"
    >
      <template v-if="completed.document_completed">
        <h1><span aria-hidden="true">{{ stateIcon.mastered }}</span> Document completed</h1>
        <p>
          With {{ session.concept_name }}, every concept of this document is mastered. Mastery
          {{ completed.mastery.toFixed(2) }}.
        </p>
      </template>
      <template v-else>
        <h1>
          <span aria-hidden="true">{{ stateIcon.mastered }}</span>
          {{ session.concept_name }} mastered
        </h1>
        <p>All key ideas answered correctly, with mastery {{ completed.mastery.toFixed(2) }}.</p>
        <template v-if="completed.newly_unlocked.length">
          <h2>Newly unlocked</h2>
          <ul class="plain-list">
            <li v-for="c in completed.newly_unlocked" :key="c.id">
              <span aria-hidden="true">{{ stateIcon.unlocked }}</span> {{ c.name }}
            </li>
          </ul>
        </template>
        <p v-else class="muted">No new concepts were unlocked by this one.</p>
      </template>
      <div class="actions centered-actions">
        <RouterLink
          :to="{ name: 'document', params: { id: session.document_id } }"
          class="button secondary"
        >
          Back to graph
        </RouterLink>
        <RouterLink
          v-if="completed.next_concept"
          :to="{ name: 'learn', params: { id: completed.next_concept.id } }"
          class="button"
        >
          Next concept: {{ completed.next_concept.name }}
        </RouterLink>
      </div>
    </section>

    <template v-else-if="session">
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
            :disabled="busy || advancing"
            @click="disputing = true"
          >
            I disagree
          </button>
          <button
            v-if="!result.answer.needs_explanation"
            :disabled="busy || advancing"
            @click="next"
          >
            {{ completed ? 'Continue' : 'Next question' }}
          </button>
        </div>
      </section>

      <section
        v-if="result?.answer.needs_explanation"
        class="learn-card explanation"
        aria-label="Explanation"
        aria-live="polite"
      >
        <div class="learn-card-head">
          <h2>Let's look at this again</h2>
          <span v-if="explanation" class="muted">{{ angleLabel[explanation.angle] }}</span>
        </div>
        <p v-if="explaining" class="muted">Writing an explanation for the gap…</p>
        <div v-else-if="explainError" class="learn-error">
          <p class="error" role="alert">{{ explainError }}</p>
          <button class="secondary" @click="explain">Retry explanation</button>
        </div>
        <template v-else-if="explanation">
          <p class="muted">
            About: {{ explanation.key_ideas.map((k) => k.text).join(' · ') }}
          </p>
          <div class="explanation-text">{{ explanation.text }}</div>
          <details v-if="explanationSources.length" class="sources">
            <summary>
              Sources ({{ explanationSources.map(chunkLocation).join(', ') }})
            </summary>
            <ol class="passages">
              <li v-for="chunk in explanationSources" :key="chunk.id">
                <div class="passage-location">{{ chunkLocation(chunk) }}</div>
                <p>{{ chunk.text }}</p>
              </li>
            </ol>
          </details>
        </template>
        <div class="actions">
          <button :disabled="busy || explaining || advancing || disputing" @click="next">
            {{ explanation ? 'Got it, ask me again' : 'Skip to the next question' }}
          </button>
        </div>
      </section>

      <section v-if="result?.way_out" class="learn-card way-out" aria-label="Stuck?">
        <h2>Stuck on this one?</h2>
        <p>
          You have missed
          <strong>{{ result.way_out.key_ideas.map((k) => k.text).join(', ') }}</strong>
          three times. You can keep trying, or take a step back:
        </p>
        <div class="actions start">
          <RouterLink
            v-if="result.way_out.prerequisite"
            :to="{ name: 'learn', params: { id: result.way_out.prerequisite.id } }"
            class="button secondary"
          >
            <span aria-hidden="true">{{ stateIcon[result.way_out.prerequisite.state] }}</span>
            <span class="visually-hidden">{{ stateLabel[result.way_out.prerequisite.state] }}: </span>
            {{ result.way_out.prerequisite.state === 'mastered' ? 'Review' : 'Learn' }}
            {{ result.way_out.prerequisite.name }} first
          </RouterLink>
          <RouterLink
            v-if="result.way_out.other_concept"
            :to="{ name: 'learn', params: { id: result.way_out.other_concept.id } }"
            class="button secondary"
          >
            Come back later – learn {{ result.way_out.other_concept.name }} now
          </RouterLink>
          <RouterLink
            v-else
            :to="{ name: 'document', params: { id: session.document_id } }"
            class="button secondary"
          >
            Come back later
          </RouterLink>
        </div>
      </section>

      <p v-if="advancing" class="learn-status muted" aria-live="polite">
        Preparing the next question…
      </p>
      <div v-if="advanceError" class="learn-error">
        <p class="error" role="alert">{{ advanceError }}</p>
        <button @click="next">Try again</button>
      </div>

      <section v-if="nothingToAsk" class="learn-card">
        <p class="muted">This concept has no key ideas to ask about.</p>
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
