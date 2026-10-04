import { createRouter, createWebHistory } from 'vue-router'
import { auth } from './api'
import LibraryView from './views/LibraryView.vue'
import LoginView from './views/LoginView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: LoginView, meta: { public: true } },
    { path: '/', name: 'library', component: LibraryView },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach(async (to) => {
  if (to.meta.public) return true
  try {
    await auth.me()
    return true
  } catch {
    return { name: 'login', query: to.fullPath === '/' ? {} : { next: to.fullPath } }
  }
})
