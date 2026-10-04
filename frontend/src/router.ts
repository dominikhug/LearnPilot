import { createRouter, createWebHistory } from 'vue-router'
import { auth } from './api'
import DocumentView from './views/DocumentView.vue'
import LearnView from './views/LearnView.vue'
import LibraryView from './views/LibraryView.vue'
import LoginView from './views/LoginView.vue'
import ProcessingView from './views/ProcessingView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: LoginView, meta: { public: true } },
    { path: '/', name: 'library', component: LibraryView },
    {
      path: '/documents/:id(\\d+)/processing',
      name: 'processing',
      component: ProcessingView,
      props: (route) => ({ id: Number(route.params.id) }),
    },
    {
      path: '/documents/:id(\\d+)',
      name: 'document',
      component: DocumentView,
      props: (route) => ({ id: Number(route.params.id) }),
    },
    {
      path: '/concepts/:id(\\d+)/learn',
      name: 'learn',
      component: LearnView,
      props: (route) => ({ id: Number(route.params.id) }),
    },
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
