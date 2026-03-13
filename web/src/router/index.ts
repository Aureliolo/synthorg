import { createRouter, createWebHistory } from 'vue-router'
import { authGuard } from './guards'
import PlaceholderHome from '@/views/PlaceholderHome.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginPage.vue'),
      meta: { requiresAuth: false },
    },
    {
      path: '/setup',
      name: 'setup',
      component: () => import('@/views/SetupPage.vue'),
      meta: { requiresAuth: false },
    },
    {
      path: '/',
      name: 'home',
      component: PlaceholderHome,
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/',
    },
  ],
})

router.beforeEach(authGuard)

export { router }
