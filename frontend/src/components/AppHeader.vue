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
    <RouterLink to="/" class="brand">LearnPilot</RouterLink>
    <button class="secondary" @click="logout">Log out</button>
  </header>
</template>
