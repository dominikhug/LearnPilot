<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { type LlmPurpose, type Tokens, type Usage, errorMessage, usage as usageApi } from '../api'
import AppHeader from '../components/AppHeader.vue'

const usage = ref<Usage>()
const error = ref('')

const purposeLabel: Record<LlmPurpose, string> = {
  concept_extraction: 'Finding concepts',
  question_plan: 'Question plans',
  grading: 'Grading',
  follow_up_question: 'Follow-up questions',
  explanation: 'Re-explanations',
}

const number = (n: number) => n.toLocaleString()
const total = (t: Tokens) => t.input_tokens + t.output_tokens

function formatTime(iso: string) {
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

onMounted(async () => {
  try {
    usage.value = await usageApi.get()
  } catch (e) {
    error.value = errorMessage(e)
  }
})
</script>

<template>
  <AppHeader />
  <main class="page usage">
    <RouterLink to="/" class="muted">← Library</RouterLink>
    <h1>AI usage</h1>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <template v-if="usage">
      <h2>Today</h2>
      <div class="mastery">
        <meter
          :value="usage.used_today"
          min="0"
          :max="usage.daily_limit"
          :high="usage.daily_limit * 0.8"
          :optimum="0"
        />
        <span>{{ number(usage.used_today) }} / {{ number(usage.daily_limit) }} tokens</span>
      </div>
      <p v-if="usage.paused" class="notice" role="status">
        The daily limit is used up. AI features resume at {{ formatTime(usage.resets_at) }}.
      </p>
      <p v-else class="muted">The limit resets at {{ formatTime(usage.resets_at) }}.</p>

      <h2>By document</h2>
      <p v-if="!usage.by_document.length" class="muted">No AI calls yet.</p>
      <table v-else class="usage-table">
        <thead>
          <tr>
            <th scope="col">Document</th>
            <th scope="col">Calls</th>
            <th scope="col">Input tokens</th>
            <th scope="col">Output tokens</th>
            <th scope="col">Total</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in usage.by_document" :key="row.document_id ?? 'deleted'">
            <th scope="row">
              <RouterLink v-if="row.document_id !== null" :to="`/documents/${row.document_id}`">
                {{ row.title }}
              </RouterLink>
              <span v-else class="muted">Deleted documents</span>
            </th>
            <td>{{ number(row.calls) }}</td>
            <td>{{ number(row.input_tokens) }}</td>
            <td>{{ number(row.output_tokens) }}</td>
            <td>{{ number(total(row)) }}</td>
          </tr>
        </tbody>
        <tfoot>
          <tr>
            <th scope="row">All time</th>
            <td>{{ number(usage.total.calls) }}</td>
            <td>{{ number(usage.total.input_tokens) }}</td>
            <td>{{ number(usage.total.output_tokens) }}</td>
            <td>{{ number(total(usage.total)) }}</td>
          </tr>
        </tfoot>
      </table>

      <h2>By purpose</h2>
      <table v-if="usage.by_purpose.length" class="usage-table">
        <thead>
          <tr>
            <th scope="col">Purpose</th>
            <th scope="col">Calls</th>
            <th scope="col">Input tokens</th>
            <th scope="col">Output tokens</th>
            <th scope="col">Total</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in usage.by_purpose" :key="row.purpose">
            <th scope="row">{{ purposeLabel[row.purpose] }}</th>
            <td>{{ number(row.calls) }}</td>
            <td>{{ number(row.input_tokens) }}</td>
            <td>{{ number(row.output_tokens) }}</td>
            <td>{{ number(total(row)) }}</td>
          </tr>
        </tbody>
      </table>
      <p class="muted hint">
        Input tokens include cached prompt parts, which cost less. Totals cover all time.
      </p>
    </template>
  </main>
</template>
