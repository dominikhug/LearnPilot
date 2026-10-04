<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError, auth } from '../api'

const route = useRoute()
const router = useRouter()
const password = ref('')
const error = ref('')
const busy = ref(false)

async function submit() {
  busy.value = true
  error.value = ''
  try {
    await auth.login(password.value)
    const next = typeof route.query.next === 'string' ? route.query.next : '/'
    await router.replace(next.startsWith('/') ? next : '/')
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'Could not reach the server.'
    password.value = ''
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="centered">
    <form class="card" @submit.prevent="submit">
      <h1>LearnPilot</h1>
      <label for="password">Password</label>
      <input
        id="password"
        v-model="password"
        type="password"
        autocomplete="current-password"
        autofocus
        required
      />
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <button type="submit" :disabled="busy || !password">Log in</button>
    </form>
  </main>
</template>
