<template>
  <div class="comfy-panel">
    <!-- ── 顶部：标题 + 分页 + 头显状态 ── -->
    <div class="comfy-header">
      <div class="header-left">
        <span class="panel-title">{{ T.title }}</span>
        <div class="tabs">
          <button v-for="(label, key) in T.tabs" :key="key" class="tab-btn"
                  :class="{ active: tab === key }" @click="tab = key">{{ label }}</button>
        </div>
      </div>
      <div class="header-right">
        <span class="conn-badge" :class="hs.presence?.online ? 'conn-ok' : 'conn-off'">
          <span class="conn-dot"></span>
          {{ hs.presence?.online ? T.hsOnline : T.hsOffline }}
          <template v-if="hs.prefetch_count"> · {{ T.hsCached }} {{ hs.cached_count }}/{{ hs.prefetch_count }}</template>
        </span>
      </div>
    </div>

    <div class="body">
      <!-- ═════════ ① 批量生成 ═════════ -->
      <template v-if="tab === 'gen'">
        <section class="sec">
          <p class="hint">{{ T.genIntro }}</p>
          <div class="field">
            <label class="field-label">{{ T.conditions }}</label>
            <div class="preset-btns">
              <button v-for="c in options.conditions" :key="c.key" class="preset-btn"
                      :class="{ active: gp.conditions.includes(c.key) }" @click="toggleCond(c.key)">
                {{ c.key }} · {{ condLabel(c.key) }}
              </button>
            </div>
          </div>
          <div class="grid4">
            <label class="field"><span class="field-label">{{ T.perCond }}</span>
              <input v-model.number="gp.per_condition" type="number" min="1" max="50" class="ctrl-num" /></label>
            <label class="field"><span class="field-label">{{ T.seed }}</span>
              <input v-model.number="gp.seed" type="number" min="-1" class="ctrl-num" /></label>
            <label class="field"><span class="field-label">{{ T.duration }}</span>
              <input v-model.number="gp.output.duration_s" type="number" min="2" max="180" class="ctrl-num" /></label>
            <label class="field"><span class="field-label">{{ T.outWidth }}</span>
              <select v-model.number="gp.output.width" class="ctrl-select">
                <option v-for="w in [1536, 2048, 2560, 3072, 3840]" :key="w" :value="w">{{ w }}×{{ w / 2 }}</option>
              </select></label>
          </div>
          <label class="check"><input v-model="gp.video.enabled" type="checkbox" /> {{ T.videoEnabled }}</label>
          <details class="fold">
            <summary>{{ T.advanced }}</summary>
            <div class="grid4">
              <label class="field"><span class="field-label">{{ T.panoSteps }}</span>
                <input v-model.number="gp.pano.steps" type="number" min="4" max="60" class="ctrl-num" /></label>
              <label class="field"><span class="field-label">{{ T.videoSteps }}</span>
                <input v-model.number="gp.video.steps" type="number" min="4" max="60" class="ctrl-num" /></label>
              <label class="field"><span class="field-label">{{ T.videoCfg }}</span>
                <input v-model.number="gp.video.cfg" type="number" min="1" max="15" step="0.5" class="ctrl-num" /></label>
              <label class="field"><span class="field-label">{{ T.frames }}</span>
                <input v-model.number="gp.video.frames" type="number" min="17" max="241" step="4" class="ctrl-num" /></label>
            </div>
          </details>
          <div class="row">
            <button class="btn-primary" :disabled="gen.running || !gp.conditions.length" @click="startGen">{{ T.start }}</button>
            <button class="btn-ghost" :disabled="!gen.running" @click="stopGen">{{ T.stop }}</button>
            <span class="muted">{{ T.total }} {{ gp.conditions.length * gp.per_condition }}</span>
          </div>
          <p v-if="genErr" class="status-msg msg-error">{{ genErr }}</p>
        </section>

        <section v-if="gen.total" class="sec">
          <div class="row">
            <span>{{ T.done }} {{ gen.done }}/{{ gen.total }}</span>
            <span v-if="gen.failed" class="bad">{{ T.failed }} {{ gen.failed }}</span>
            <span v-if="gen.avg_pair_s" class="muted">{{ T.perPair }} {{ fmtSec(gen.avg_pair_s) }}</span>
            <span v-if="gen.running && gen.eta_s" class="muted">{{ T.eta }} {{ fmtSec(gen.eta_s) }}</span>
          </div>
          <div class="progress-bar"><div class="progress-fill" :style="{ width: genPct + '%' }"></div></div>
          <p v-if="gen.current" class="muted">
            #{{ gen.current.index }} {{ gen.current.condition }} · {{ gen.current.scene }} ·
            {{ T.stepNames[gen.current.step] || gen.current.step }}
          </p>
          <div class="log">
            <div v-for="l in [...gen.log].reverse()" :key="l.ts + l.msg">{{ fmtTime(l.ts) }} {{ l.msg }}</div>
          </div>
        </section>
      </template>

      <!-- ═════════ ② 刺激库 ═════════ -->
      <template v-if="tab === 'lib'">
        <div class="row">
          <div class="preset-btns">
            <button class="preset-btn" :class="{ active: libFilter === '' }" @click="libFilter = ''">{{ T.filterAll }}</button>
            <button v-for="c in options.conditions" :key="c.key" class="preset-btn"
                    :class="{ active: libFilter === c.key }" @click="libFilter = c.key">{{ c.key }}</button>
          </div>
          <span class="muted">{{ filteredItems.length }}</span>
          <button class="icon-btn" @click="loadItems">↻</button>
        </div>
        <p v-if="!items.length" class="muted">{{ T.empty }}</p>
        <div class="lib-grid">
          <div v-for="m in filteredItems" :key="m.pair_id" class="card" :class="'qc-' + (m.qc || 'pending')">
            <img v-if="m.urls.thumb" :src="m.urls.thumb" class="thumb" @click="panoSrc = m.urls.still" />
            <div class="card-body">
              <div class="row tight">
                <span class="tag">{{ m.condition }}</span>
                <span class="scene">{{ m.scene }}</span>
                <span v-if="!m.urls.video" class="tag warn">{{ T.imageOnly }}</span>
              </div>
              <div class="muted small">{{ m.pair_id }}</div>
              <div v-if="m.features?.video || m.features?.image" class="muted small">
                {{ T.lum }} {{ fmt(m.features.image?.luminance) }} · {{ T.contrast }} {{ fmt(m.features.image?.contrast) }}
                <template v-if="m.features.video"> · {{ T.motion }} {{ fmt(m.features.video.motion, 4) }}</template>
              </div>
              <div class="row tight">
                <button class="mini" @click="panoSrc = m.urls.still">{{ T.preview }} {{ T.image }}</button>
                <button v-if="m.urls.video" class="mini" @click="videoSrc = m.urls.video">{{ T.preview }} {{ T.video }}</button>
              </div>
              <div class="row tight">
                <button class="mini accent" @click="show(m.pair_id + ':image')">{{ T.toHeadset }}·{{ T.image }}</button>
                <button v-if="m.urls.video" class="mini accent" @click="show(m.pair_id + ':video')">{{ T.toHeadset }}·{{ T.video }}</button>
              </div>
              <div class="row tight">
                <button v-for="q in ['ok', 'reject', 'pending']" :key="q" class="mini"
                        :class="{ on: (m.qc || 'pending') === q, ['q-' + q]: true }" @click="qc(m, q)">{{ T.qc[q] }}</button>
                <button class="mini danger" @click="remove(m)">{{ T.delete }}</button>
              </div>
            </div>
          </div>
        </div>
      </template>

      <!-- ═════════ ③ 播放控制 ═════════ -->
      <template v-if="tab === 'play'">
        <section class="sec">
          <div class="sec-title">{{ T.current }}</div>
          <div class="row">
            <span class="tag">{{ T.phases[pb.phase] || pb.phase }}</span>
            <span>{{ pb.stimulus ? pb.stimulus.id : T.none }}</span>
            <span v-if="pb.stimulus" class="muted">{{ pb.stimulus.line === 'video' ? T.video : T.image }} · {{ pb.stimulus.condition }}</span>
            <span v-if="pb.paused" class="tag warn">{{ T.pause }}</span>
          </div>
          <div v-if="pb.phase_duration_s" class="progress-bar">
            <div class="progress-fill" :style="{ width: phasePct + '%' }"></div>
          </div>
          <p v-if="pb.waiting_headset" class="muted">{{ T.waitingHeadset }}</p>
          <p class="muted small">
            headset: {{ hs.state || '-' }} {{ hs.stimulus_id }} · {{ (hs.position_s || 0).toFixed(1) }}s
            <span v-if="hs.error" class="bad"> · {{ hs.error }}</span>
          </p>
        </section>

        <section class="sec">
          <div class="sec-title">{{ T.manual }}</div>
          <div class="row">
            <button class="btn-ghost" :disabled="pbBusy || pb.mode === 'idle'" @click="cmd(pb.paused ? 'resume' : 'pause')">
              {{ pb.paused ? T.resume : T.pause }}</button>
            <button class="btn-ghost" :disabled="pbBusy || pb.mode === 'sequence'" @click="cmd('blank')">{{ T.blank }}</button>
            <button class="btn-ghost" :disabled="pbBusy || pb.mode !== 'manual' || !pb.stimulus" @click="cmd('rate')">{{ T.rate }}</button>
            <button class="btn-ghost danger" :disabled="pbBusy || pb.mode === 'idle'" @click="cmd('stop')">{{ T.stopAll }}</button>
          </div>
          <div class="row">
            <span class="field-label">{{ T.seek }}</span>
            <input v-model.number="seekTo" type="number" min="0" class="ctrl-num small-num" />
            <button class="btn-ghost" :disabled="pbBusy || pb.mode !== 'manual' || pb.stimulus?.media !== 'video'"
                    @click="cmd('seek', { pos_s: seekTo })">{{ T.go }}</button>
          </div>
          <div class="row">
            <button class="btn-ghost" :disabled="pbBusy" @click="prefetchAll">{{ T.prefetchAll }}</button>
            <span class="muted small">{{ T.prefetchHint }}</span>
          </div>
        </section>

        <section class="sec">
          <div class="sec-title">{{ T.sequence }}</div>
          <div class="grid4">
            <label class="field"><span class="field-label">{{ T.subject }}</span>
              <input v-model="sp.subject_id" type="text" class="ctrl-num wide" /></label>
            <label class="field"><span class="field-label">{{ T.group }}</span>
              <select v-model="sp.cb_group" class="ctrl-select">
                <option v-for="(label, k) in T.groupOpts" :key="k" :value="k">{{ label }}</option>
              </select></label>
            <label class="field"><span class="field-label">{{ T.seed }}</span>
              <input v-model.number="sp.seed" type="number" min="-1" class="ctrl-num" /></label>
            <label class="check"><input v-model="sp.qc_only" type="checkbox" /> {{ T.qcOnly }}</label>
          </div>
          <p class="hint">{{ T.groupHint }}</p>
          <div class="grid4">
            <label class="field"><span class="field-label">{{ T.fixation }}</span>
              <input v-model.number="sp.fixation_s" type="number" min="0" class="ctrl-num" /></label>
            <label class="field"><span class="field-label">{{ T.stimulus }}</span>
              <input v-model.number="sp.stimulus_s" type="number" min="1" class="ctrl-num" /></label>
            <label class="field"><span class="field-label">{{ T.ratingTimeout }}</span>
              <input v-model.number="sp.rating_timeout_s" type="number" min="0" class="ctrl-num" /></label>
            <label class="field"><span class="field-label">{{ T.recovery }}</span>
              <input v-model.number="sp.recovery_s" type="number" min="0" class="ctrl-num" /></label>
          </div>
          <div class="preset-btns">
            <button v-for="c in options.conditions" :key="c.key" class="preset-btn"
                    :class="{ active: sp.conditions.includes(c.key) }" @click="toggleSeqCond(c.key)">{{ c.key }}</button>
          </div>
          <div class="row">
            <button class="btn-primary" :disabled="pbBusy || pb.mode === 'sequence'" @click="startSeq">{{ T.startSeq }}</button>
            <button class="btn-ghost" :disabled="pbBusy || pb.mode !== 'sequence'" @click="skip">{{ T.skip }}</button>
            <button class="btn-ghost" :disabled="pbBusy || pb.mode !== 'sequence'" @click="cmd(pb.paused ? 'resume' : 'pause')">
              {{ pb.paused ? T.resume : T.pause }}</button>
            <button class="btn-ghost danger" :disabled="pbBusy || pb.mode !== 'sequence'" @click="cmd('stop')">{{ T.stopAll }}</button>
            <span v-if="pb.run" class="muted">{{ pb.run.run_id }} · {{ T.trial }} {{ pb.run.index }}/{{ pb.run.total }}</span>
          </div>
          <p v-if="pbErr" class="status-msg msg-error">{{ pbErr }}</p>
          <table v-if="seqList.length" class="tbl">
            <thead><tr><th>{{ T.trial }}</th><th>id</th><th></th><th></th><th></th><th>V / A</th></tr></thead>
            <tbody>
              <tr v-for="it in seqList" :key="it.trial" :class="{ cur: pb.run && it.trial === pb.run.index }">
                <td>{{ it.trial }}</td><td class="mono">{{ it.stimulus_id }}</td>
                <td>{{ it.line === 'video' ? T.video : T.image }}</td><td>{{ it.condition }}</td>
                <td>{{ T.itemStatus[it.status] || it.status }}</td>
                <td>{{ it.rating ? `${it.rating.valence} / ${it.rating.arousal}` : '' }}</td>
              </tr>
            </tbody>
          </table>
        </section>
      </template>

      <!-- ═════════ ④ 方案对比 ═════════ -->
      <template v-if="tab === 'cmp'">
        <div class="row">
          <button class="btn-ghost" @click="loadCompare">{{ T.refresh }}</button>
          <label class="check"><input v-model="includeManual" type="checkbox" @change="loadCompare" /> {{ T.includeManual }}</label>
        </div>
        <p v-if="cmpErr" class="status-msg msg-error">{{ cmpErr }}</p>
        <template v-if="cmp">
          <p v-if="!cmp.n_ratings" class="muted">{{ T.noData }}</p>
          <section class="sec">
            <div class="sec-title">{{ T.verdict }}</div>
            <p v-for="(v, i) in cmp.verdict" :key="i" class="verdict">{{ v }}</p>
          </section>
          <section class="sec">
            <table class="tbl">
              <thead><tr><th>{{ T.metric }}</th><th>{{ T.imageLine }}</th><th>{{ T.videoLine }}</th>
                <th>{{ T.diff }}</th><th>{{ T.pairs }}</th><th>p</th></tr></thead>
              <tbody>
                <tr v-for="m in ['hit', 'arousal', 'extremity', 'fea_expr']" :key="m">
                  <td>{{ T.metrics[m] }}</td>
                  <td>{{ fmt(cmp.lines.image[m]) }}</td><td>{{ fmt(cmp.lines.video[m]) }}</td>
                  <td>{{ fmtSigned(cmp.paired[m].mean_diff) }}</td><td>{{ cmp.paired[m].n_pairs }}</td>
                  <td :class="{ good: cmp.paired[m].p != null && cmp.paired[m].p < 0.05 }">{{ fmt(cmp.paired[m].p) }}</td>
                </tr>
                <tr><td>{{ T.presentations }}</td><td>{{ cmp.lines.image.n_presentations }}</td><td>{{ cmp.lines.video.n_presentations }}</td><td colspan="3"></td></tr>
                <tr><td>{{ T.subjects }}</td><td>{{ cmp.lines.image.n_subjects }}</td><td>{{ cmp.lines.video.n_subjects }}</td><td colspan="3"></td></tr>
                <tr><td>{{ T.sdV }}</td><td>{{ fmt(cmp.lines.image.sd_v) }}</td><td>{{ fmt(cmp.lines.video.sd_v) }}</td><td colspan="3"></td></tr>
                <tr><td>{{ T.sdA }}</td><td>{{ fmt(cmp.lines.image.sd_a) }}</td><td>{{ fmt(cmp.lines.video.sd_a) }}</td><td colspan="3"></td></tr>
              </tbody>
            </table>
          </section>
          <section class="sec">
            <div class="sec-title">{{ T.byCondition }}</div>
            <table class="tbl">
              <thead><tr><th></th><th v-for="c in options.conditions" :key="c.key">{{ c.key }}</th></tr></thead>
              <tbody>
                <tr v-for="line in ['image', 'video']" :key="line">
                  <td>{{ line === 'image' ? T.imageLine : T.videoLine }}</td>
                  <td v-for="c in options.conditions" :key="c.key">
                    {{ fmt(cmp.lines[line].by_condition[c.key]?.hit) }}
                    <span class="muted small">(n={{ cmp.lines[line].by_condition[c.key]?.n || 0 }})</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </section>
          <section class="sec">
            <div class="sec-title">{{ T.lowlevel }}</div>
            <table class="tbl">
              <thead><tr><th></th><th>{{ T.lum }}</th><th>{{ T.contrast }}</th><th>colorfulness</th><th>{{ T.motion }}</th></tr></thead>
              <tbody>
                <tr v-for="line in ['image', 'video']" :key="line">
                  <td>{{ line === 'image' ? T.imageLine : T.videoLine }}</td>
                  <td v-for="k in ['luminance', 'contrast', 'colorfulness', 'motion']" :key="k">
                    {{ fmt(cmp.lines[line].lowlevel?.[k], k === 'motion' ? 4 : 3) }}</td>
                </tr>
                <tr>
                  <td>{{ T.diff }}</td>
                  <td v-for="k in ['luminance', 'contrast', 'colorfulness', 'motion']" :key="k">
                    {{ fmtSigned(cmp.lowlevel_diff[k]?.mean_diff, k === 'motion' ? 4 : 3) }}</td>
                </tr>
              </tbody>
            </table>
          </section>
        </template>
      </template>
    </div>

    <!-- 平面预览：视频（等距柱状直接平铺，便于检查接缝和瑕疵） -->
    <div v-if="videoSrc" class="overlay" @click.self="videoSrc = null">
      <div class="video-box">
        <video :src="videoSrc" controls autoplay loop muted class="video"></video>
        <button class="icon-btn close" @click="videoSrc = null">✕</button>
      </div>
    </div>
    <PanoramaViewer v-if="panoSrc" :src="panoSrc" caption="" :enter-vr-label="T.preview"
                    :download-label="'↓'" @close="panoSrc = null" />
  </div>
</template>

<script setup>
import { computed, defineAsyncComponent, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import * as api from '../api/stimlib.js'

const PanoramaViewer = defineAsyncComponent(() => import('./PanoramaViewer.vue'))

const props = defineProps({ locale: { type: Object, required: true } })
const T = computed(() => props.locale.stimlib)
const lang = computed(() => (props.locale.langSwitchPath === '/cn' ? 'ja' : 'zh'))

const tab = ref('gen')
const options = reactive({ conditions: [], scenes: {} })
const condLabel = (key) => options.conditions.find((c) => c.key === key)?.[lang.value] ?? key

// ── 工具 ──
const fmt = (v, d = 3) => (v == null ? '–' : Number(v).toFixed(d))
const fmtSigned = (v, d = 3) => (v == null ? '–' : (v >= 0 ? '+' : '') + Number(v).toFixed(d))
const fmtSec = (s) => (s >= 3600 ? `${(s / 3600).toFixed(1)}h` : s >= 60 ? `${Math.round(s / 60)}min` : `${Math.round(s)}s`)
const fmtTime = (ms) => new Date(ms).toLocaleTimeString()

// ── ① 批量生成 ──
const gp = reactive({
  conditions: [], per_condition: 4, seed: -1,
  pano: { steps: 20 }, video: { enabled: true, steps: 20, cfg: 5.0, frames: 121 },
  output: { width: 2048, duration_s: 30 },
})
const gen = reactive({ running: false, total: 0, done: 0, failed: 0, current: null, log: [], avg_pair_s: null, eta_s: null })
const genErr = ref('')
const genPct = computed(() => (gen.total ? Math.round(((gen.done + gen.failed) / gen.total) * 100) : 0))

function toggleCond(k) {
  const i = gp.conditions.indexOf(k)
  if (i >= 0) gp.conditions.splice(i, 1)
  else gp.conditions.push(k)
}

async function loadGen() {
  try {
    const s = await api.genStatus()
    const finished = gen.running && !s.running
    const progressed = s.done !== gen.done
    Object.assign(gen, s)
    if (finished || progressed) loadItems()
  } catch { /* 后端暂时不可达 */ }
}

async function startGen() {
  genErr.value = ''
  try {
    Object.assign(gen, await api.genStart(JSON.parse(JSON.stringify(gp))))
  } catch (e) { genErr.value = e.message }
}

async function stopGen() {
  try { Object.assign(gen, await api.genStop()) } catch (e) { genErr.value = e.message }
}

// ── ② 刺激库 ──
const items = ref([])
const libFilter = ref('')
const filteredItems = computed(() => items.value.filter((m) => !libFilter.value || m.condition === libFilter.value))
const panoSrc = ref(null)
const videoSrc = ref(null)

async function loadItems() {
  try { items.value = (await api.listItems()).items } catch { /* 忽略 */ }
}

async function qc(m, q) {
  try {
    const upd = await api.setQc(m.pair_id, q)
    items.value = items.value.map((x) => (x.pair_id === upd.pair_id ? upd : x))
  } catch (e) { alert(e.message) }
}

async function remove(m) {
  if (!window.confirm(T.value.deleteConfirm)) return
  try {
    await api.deletePair(m.pair_id)
    items.value = items.value.filter((x) => x.pair_id !== m.pair_id)
  } catch (e) { alert(e.message) }
}

async function show(stimId) {
  try {
    Object.assign(pb, await api.playbackCmd('show', { stimulus_id: stimId, subject_id: sp.subject_id }))
    tab.value = 'play'
  } catch (e) { alert(e.message) }
}

// ── ③ 播放控制 ──
const pb = reactive({ mode: 'idle', phase: 'idle', stimulus: null, paused: false, run: null,
  phase_duration_s: null, phase_elapsed_s: 0, waiting_headset: false })
const hs = reactive({ presence: null, state: '', stimulus_id: '', position_s: 0, cached_count: 0, prefetch_count: 0, error: '' })
const pbBusy = ref(false)
const pbErr = ref('')
const seekTo = ref(0)
const seqList = ref([])
const sp = reactive({ subject_id: '', cb_group: 'A', seed: -1, qc_only: false, conditions: [],
  fixation_s: 5, stimulus_s: 30, rating_timeout_s: 0, recovery_s: 10 })
const phasePct = computed(() =>
  pb.phase_duration_s ? Math.min(100, Math.round((pb.phase_elapsed_s / pb.phase_duration_s) * 100)) : 0)

function toggleSeqCond(k) {
  const i = sp.conditions.indexOf(k)
  if (i >= 0) sp.conditions.splice(i, 1)
  else sp.conditions.push(k)
}

async function pollPlayback() {
  try {
    Object.assign(pb, await api.playbackState())
    Object.assign(hs, await api.headsetStatus())
    if (pb.mode === 'sequence' || (pb.run && !seqList.value.length)) seqList.value = (await api.seqItems()).items
  } catch { /* 忽略 */ }
}

async function run(fn) {
  pbBusy.value = true
  pbErr.value = ''
  try {
    const s = await fn()
    if (s && 'phase' in s) Object.assign(pb, s)
  } catch (e) { pbErr.value = e.message } finally { pbBusy.value = false }
}

const cmd = (c, extra = {}) => run(() => api.playbackCmd(c, extra))
const prefetchAll = () => run(() => api.prefetch('all'))
const skip = () => run(api.seqSkip)
async function startSeq() {
  await run(() => api.seqStart(JSON.parse(JSON.stringify(sp))))
  seqList.value = (await api.seqItems()).items
}

// ── ④ 方案对比 ──
const cmp = ref(null)
const cmpErr = ref('')
const includeManual = ref(false)
async function loadCompare() {
  cmpErr.value = ''
  try { cmp.value = await api.compareLines(includeManual.value) } catch (e) { cmpErr.value = e.message }
}
watch(tab, (t) => { if (t === 'cmp') loadCompare(); if (t === 'lib') loadItems() })

// ── 生命周期 ──
let genTimer = null
let pbTimer = null
onMounted(async () => {
  try {
    const o = await api.getOptions()
    options.conditions = o.conditions
    options.scenes = o.scenes
    gp.conditions = o.conditions.map((c) => c.key)
    Object.assign(sp, { ...o.seq_defaults })
  } catch (e) { genErr.value = e.message }
  loadGen()
  loadItems()
  pollPlayback()
  genTimer = setInterval(loadGen, 3000)
  pbTimer = setInterval(pollPlayback, 1000)
})
onUnmounted(() => { clearInterval(genTimer); clearInterval(pbTimer) })
</script>

<style scoped>
.comfy-panel {
  flex: 1; display: flex; flex-direction: column; min-height: 0;
  background: var(--color-surface); border: 1px solid var(--color-border);
  border-radius: var(--radius); overflow: hidden;
}
.comfy-header {
  display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap;
  padding: 12px 16px; border-bottom: 1px solid var(--color-border); background: var(--color-surface-2);
}
.header-left { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.panel-title { font-weight: 700; font-size: 0.95rem; }
.tabs { display: flex; gap: 4px; flex-wrap: wrap; }
.tab-btn {
  padding: 5px 12px; border-radius: 6px; border: 1px solid var(--color-border);
  background: transparent; color: var(--color-text-muted); font-size: 0.78rem; cursor: pointer;
}
.tab-btn.active { border-color: #2563eb; color: #fff; background: #2563eb; }
.conn-badge { display: inline-flex; align-items: center; gap: 6px; font-size: 0.75rem; color: var(--color-text-muted); }
.conn-dot { width: 8px; height: 8px; border-radius: 50%; background: #666; }
.conn-ok .conn-dot { background: #2ecc71; }
.conn-off .conn-dot { background: #ff6b6b; }

.body { flex: 1; min-height: 0; overflow-y: auto; padding: 14px 16px 24px; display: flex; flex-direction: column; gap: 10px; }
.sec {
  border: 1px solid var(--color-border); border-radius: var(--radius); background: var(--color-surface-2);
  padding: 10px 12px; display: flex; flex-direction: column; gap: 8px;
}
.sec-title { font-size: 0.78rem; font-weight: 700; letter-spacing: 0.04em; }
.hint { font-size: 0.75rem; color: var(--color-text-muted); line-height: 1.5; }
.muted { color: var(--color-text-muted); font-size: 0.78rem; }
.small { font-size: 0.7rem; }
.bad { color: #ff6b6b; }
.good { color: #2ecc71; font-weight: 700; }
.mono { font-family: Consolas, monospace; font-size: 0.72rem; }
.row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.row.tight { gap: 4px; }
.grid4 { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; align-items: end; }
.field { display: flex; flex-direction: column; gap: 4px; }
.field-label { font-size: 0.72rem; font-weight: 600; color: var(--color-text-muted); }
.check { display: inline-flex; align-items: center; gap: 6px; font-size: 0.78rem; color: var(--color-text-muted); }
.fold > summary { cursor: pointer; font-size: 0.75rem; color: var(--color-text-muted); margin-bottom: 6px; }

.ctrl-num, .ctrl-select {
  background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 8px;
  color: var(--color-text); font-size: 0.82rem; padding: 6px 9px; outline: none; width: 100%;
}
.small-num { width: 90px; }
.preset-btns { display: flex; gap: 5px; flex-wrap: wrap; }
.preset-btn {
  padding: 4px 10px; border-radius: 6px; border: 1px solid var(--color-border); background: transparent;
  color: var(--color-text-muted); font-size: 0.72rem; cursor: pointer;
}
.preset-btn.active { border-color: #2563eb; color: #2563eb; background: rgba(37, 99, 235, 0.1); }
.btn-primary {
  padding: 8px 18px; border: none; border-radius: 8px; background: #2563eb; color: #fff;
  font-size: 0.85rem; font-weight: 700; cursor: pointer;
}
.btn-ghost {
  padding: 7px 14px; border: 1px solid var(--color-border); border-radius: 8px; background: transparent;
  color: var(--color-text); font-size: 0.8rem; cursor: pointer;
}
.btn-primary:disabled, .btn-ghost:disabled { opacity: 0.45; cursor: not-allowed; }
.btn-ghost.danger, .mini.danger { color: #ff6b6b; border-color: rgba(255, 107, 107, 0.4); }
.icon-btn { background: transparent; border: 1px solid var(--color-border); border-radius: 6px; color: var(--color-text); cursor: pointer; padding: 2px 8px; }

.progress-bar { height: 5px; border-radius: 3px; background: var(--color-border); overflow: hidden; }
.progress-fill { height: 100%; background: #2563eb; transition: width 0.3s ease; }
.status-msg { font-size: 0.78rem; border-radius: 6px; padding: 7px 10px; }
.msg-error { background: rgba(255, 107, 107, 0.12); color: #ff6b6b; }
.log {
  max-height: 180px; overflow-y: auto; font-family: Consolas, monospace; font-size: 0.7rem;
  color: var(--color-text-muted); background: var(--color-surface); border-radius: 6px; padding: 6px 8px;
}

.lib-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px; }
.card { border: 1px solid var(--color-border); border-radius: var(--radius); background: var(--color-surface-2); overflow: hidden; }
.card.qc-ok { border-color: rgba(46, 204, 113, 0.5); }
.card.qc-reject { opacity: 0.55; border-color: rgba(255, 107, 107, 0.5); }
.thumb { width: 100%; aspect-ratio: 2 / 1; object-fit: cover; display: block; cursor: zoom-in; }
.card-body { padding: 8px; display: flex; flex-direction: column; gap: 5px; }
.tag { font-size: 0.68rem; padding: 1px 7px; border-radius: 4px; background: rgba(37, 99, 235, 0.18); color: #8fb0ff; }
.tag.warn { background: rgba(240, 180, 60, 0.18); color: #f0b43c; }
.scene { font-size: 0.8rem; }
.mini {
  padding: 3px 8px; border-radius: 5px; border: 1px solid var(--color-border); background: transparent;
  color: var(--color-text-muted); font-size: 0.7rem; cursor: pointer;
}
.mini.accent { border-color: #2563eb; color: #8fb0ff; }
.mini.on.q-ok { background: rgba(46, 204, 113, 0.18); color: #2ecc71; border-color: #2ecc71; }
.mini.on.q-reject { background: rgba(255, 107, 107, 0.18); color: #ff6b6b; border-color: #ff6b6b; }
.mini.on.q-pending { background: rgba(255, 255, 255, 0.08); color: var(--color-text); }

.tbl { width: 100%; border-collapse: collapse; font-size: 0.76rem; }
.tbl th, .tbl td { padding: 5px 8px; border-bottom: 1px solid var(--color-border); text-align: left; }
.tbl th { color: var(--color-text-muted); font-weight: 600; }
.tbl tr.cur td { background: rgba(37, 99, 235, 0.12); }
.verdict { font-size: 0.82rem; line-height: 1.6; }

.overlay { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.8); display: flex; align-items: center; justify-content: center; z-index: 50; }
.video-box { position: relative; width: min(1400px, 94vw); }
.video { width: 100%; border-radius: 8px; background: #000; }
.close { position: absolute; top: 8px; right: 8px; }
</style>
