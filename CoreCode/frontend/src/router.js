import { createRouter, createWebHistory } from 'vue-router'
import LoginPanel from './components/LoginPanel.vue'
import TrainingPanel from './components/TrainingPanel.vue'
import EvaluationPanel from './components/EvaluationPanel.vue'
import VRStimulusPanel from './components/VRStimulusPanel.vue'
import AffectPreferencePanel from './components/AffectPreferencePanel.vue'
import StimulusLibraryPanel from './components/StimulusLibraryPanel.vue'
import SystemSettingsPanel from './components/SystemSettingsPanel.vue'
import zh from './locales/zh.js'
import ja from './locales/ja.js'
import { getAuthStatus } from './api/auth.js'

const routes = [
  { path: '/',            redirect: '/cn' },
  { path: '/cn/login',    component: LoginPanel,       props: { locale: zh } },
  { path: '/jp/login',    component: LoginPanel,       props: { locale: ja } },
  { path: '/cn',          redirect: '/cn/experiment' },
  { path: '/jp',          redirect: '/jp/experiment' },
  { path: '/cn/train',    component: TrainingPanel,    props: { locale: zh } },
  { path: '/jp/train',    component: TrainingPanel,    props: { locale: ja } },
  { path: '/cn/eval',     component: EvaluationPanel,  props: { locale: zh } },
  { path: '/jp/eval',     component: EvaluationPanel,  props: { locale: ja } },
  { path: '/cn/comfyui/stimulus', component: VRStimulusPanel, props: { locale: zh } },
  { path: '/jp/comfyui/stimulus', component: VRStimulusPanel, props: { locale: ja } },
  { path: '/cn/comfyui/affect', component: AffectPreferencePanel, props: { locale: zh } },
  { path: '/jp/comfyui/affect', component: AffectPreferencePanel, props: { locale: ja } },
  { path: '/cn/experiment', component: StimulusLibraryPanel, props: { locale: zh } },
  { path: '/jp/experiment', component: StimulusLibraryPanel, props: { locale: ja } },
  { path: '/cn/comfyui/stimlib', redirect: '/cn/experiment' },
  { path: '/jp/comfyui/stimlib', redirect: '/jp/experiment' },
  { path: '/cn/settings',  component: SystemSettingsPanel, props: { locale: zh } },
  { path: '/jp/settings',  component: SystemSettingsPanel, props: { locale: ja } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 未登录一律送去登录页。每次导航问一次后端（一个很小的 GET），省掉本地缓存
// 状态失效的那堆麻烦；后端没配口令时 required=false，等于不拦。
router.beforeEach(async (to) => {
  if (to.path.endsWith('/login')) return true
  try {
    const s = await getAuthStatus()
    if (s.required && !s.authenticated) {
      const prefix = to.path.startsWith('/jp') ? '/jp' : '/cn'
      return { path: `${prefix}/login`, query: { redirect: to.fullPath } }
    }
  } catch { /* 后端没起来：不拦，页面自己会报错 */ }
  return true
})

export default router
