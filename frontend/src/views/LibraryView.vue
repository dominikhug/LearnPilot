<script setup lang="ts">
import { useRouter } from 'vue-router'
import { auth } from '../api'

const router = useRouter()

async function logout() {
  await auth.logout()
  await router.replace({ name: 'login' })
}
</script>

<template>
  <header class="topbar">
    <strong>LearnPilot</strong>
    <button class="secondary" @click="logout">Log out</button>
  </header>
  <main class="page">
    <h1>Library</h1>
    <p class="muted">No documents yet. Uploading arrives in the next step.</p>
  </main>
</template>
