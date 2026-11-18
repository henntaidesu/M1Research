<template>
  <div class="comfy-panel">
    <!-- ── 顶部：标题 + 步骤 + 头显状态 ── -->
    <div class="comfy-header">
      <div class="header-left">
        <span class="panel-title">{{ T.title }}</span>
        <div class="tabs">
          <button v-for="(label, key) in T.tabs" :key="key" class="tab-btn"
                  :class="{ active: tab === key, adv: key === 'cmp' }" @click="tab = key">{{ label }}</button>
        </div>
      </div>
      <div class="header-right">
        <span class="conn-badge" :class="hs.presence?.online ? 'conn-ok' : 'conn-off'">
          <span class="conn-dot"></span>{{ hs.presence?.online ? T.hsOnline : T.hsOffline }}
        </span>
        <span class="conn-badge" :class="hs.fea_hz > 20 ? 'conn-ok' : 'conn-off'">
          <span class="conn-dot"></span>FEA {{ hs.fea_hz || 0 }} Hz
        </span>
      </div>
    </div>

    <div class="body">
      <!-- ═════════ ① 场景预设 ═════════ -->
      <template v-if="tab === 'scenes'">
        <p class="hint">{{ T.scenesIntro }}</p>
        <section class="sec">
          <table class="tbl scenes">
            <thead><tr>
              <th>{{ T.sceneNo }}</th><th>{{ T.sceneName }}</th><th>{{ T.condition }}</th><th>{{ T.prompt }}</th>
              <th>{{ T.selectedImg }}</th><th></th>
            </tr></thead>
            <tbody>
              <template v-for="(s, i) in scenes" :key="s.id">
                <tr v-if="editId !== s.id">
                  <td class="nowrap"><b>{{ T.scenePrefix }}{{ s.no }}</b><div class="muted small">{{ s.id }}</div></td>
                  <td>{{ s.name }}</td>
                  <td><span class="tag" :title="condLabel(s.condition)">{{ s.condition }}</span>
                    <div class="muted small">{{ condLabel(s.condition) }}</div></td>
                  <td class="prompt-cell">{{ s.prompt }}</td>
                  <td>
                    <img v-if="s.selected" :src="s.selected.urls.thumb" class="mini-thumb" @click="panoSrc = s.selected.urls.still" />
                    <span v-else class="muted small">{{ T.noneSelected }}（{{ s.n_candidates }} {{ T.candidates }}）</span>
                  </td>
                  <td class="nowrap">
                    <button class="mini" :disabled="i === 0" @click="move(i, -1)">↑</button>
                    <button class="mini" :disabled="i === scenes.length - 1" @click="move(i, 1)">↓</button>
                    <button class="mini" @click="startEdit(s)">{{ T.edit }}</button>
                    <button class="mini danger" @click="removeScene(s)">{{ T.delete }}</button>
                  </td>
                </tr>
                <tr v-else class="editing"><td colspan="6"><div class="edit-form">
                  <div class="grid4">
                    <label class="field"><span class="field-label">{{ T.sceneName }}</span>
                      <input v-model="form.name" class="ctrl-num wide" /></label>
                    <label class="field"><span class="field-label">{{ T.condition }}</span>
                      <select v-model="form.condition" class="ctrl-select">
                        <option v-for="c in options.conditions" :key="c.key" :value="c.key">{{ c.key }} · {{ condLabel(c.key) }}</option>
                      </select></label>
                  </div>
                  <label class="field"><span class="field-label">{{ T.prompt }}</span>
                    <textarea v-model="form.prompt" rows="3" class="ctrl-area"></textarea></label>
                  <label class="field"><span class="field-label">{{ T.motion }}</span>
                    <input v-model="form.motion" class="ctrl-num wide" /></label>
                  <div class="row">
                    <button class="btn-primary" @click="saveEdit">{{ T.save }}</button>
                    <button class="btn-ghost" @click="editId = null">{{ T.cancel }}</button>
                    <span v-if="sceneErr" class="bad">{{ sceneErr }}</span>
                  </div>
                </div></td></tr>
              </template>
              <tr v-if="editId === '__new__'" class="editing"><td colspan="6"><div class="edit-form">
                <div class="grid4">
                  <label class="field"><span class="field-label">{{ T.sceneName }}</span>
                    <input v-model="form.name" class="ctrl-num wide" /></label>
                  <label class="field"><span class="field-label">{{ T.condition }}</span>
                    <select v-model="form.condition" class="ctrl-select">
                      <option v-for="c in options.conditions" :key="c.key" :value="c.key">{{ c.key }} · {{ condLabel(c.key) }}</option>
                    </select></label>
                </div>
                <label class="field"><span class="field-label">{{ T.prompt }}</span>
                  <textarea v-model="form.prompt" rows="3" class="ctrl-area" :placeholder="T.promptPh"></textarea></label>
                <label class="field"><span class="field-label">{{ T.motion }}</span>
                  <input v-model="form.motion" class="ctrl-num wide" :placeholder="T.motionPh" /></label>
                <div class="row">
                  <button class="btn-primary" @click="saveEdit">{{ T.save }}</button>
                  <button class="btn-ghost" @click="editId = null">{{ T.cancel }}</button>
                  <span v-if="sceneErr" class="bad">{{ sceneErr }}</span>
                </div>
              </div></td></tr>
            </tbody>
          </table>
          <div class="row">
            <button class="btn-primary" @click="startNew">＋ {{ T.newScene }}</button>
            <select v-model="tplPick" class="ctrl-select tpl-select">
              <option value="">{{ T.fromTemplate }}</option>
              <optgroup v-for="c in options.conditions" :key="c.key" :label="`${c.key} · ${condLabel(c.key)}`">
                <option v-for="t in templates.filter((x) => x.condition === c.key)" :key="t.template" :value="t.template">{{ t.name }}</option>
              </optgroup>
            </select>
            <button class="btn-ghost" :disabled="!tplPick" @click="addTemplate">{{ T.add }}</button>
          </div>
        </section>
      </template>

      <!-- ═════════ ② 生成图片 ═════════ -->
      <template v-if="tab === 'gen'">
        <section class="sec">
          <p class="hint">{{ T.genIntro }}</p>
          <div class="field">
            <div class="row"><span class="field-label">{{ T.genScenes }}</span>
              <button class="mini" @click="gp.scene_ids = scenes.map((s) => s.id)">{{ T.all }}</button>
              <button class="mini" @click="gp.scene_ids = scenes.filter((s) => !s.selected).map((s) => s.id)">{{ T.onlyUnselected }}</button>
            </div>
            <div class="preset-btns">
              <button v-for="s in scenes" :key="s.id" class="preset-btn" :class="{ active: gp.scene_ids.includes(s.id) }"
                      @click="toggle(gp.scene_ids, s.id)">{{ T.scenePrefix }}{{ s.no }} {{ s.name }}
                <span class="muted small">({{ s.n_candidates }})</span></button>
            </div>
          </div>
          <div class="grid4">
            <label class="field"><span class="field-label">{{ T.perScene }}</span>
              <input v-model.number="gp.per_scene" type="number" min="1" max="20" class="ctrl-num" /></label>
            <label class="field"><span class="field-label">{{ T.seed }}</span>
              <input v-model.number="gp.seed" type="number" min="-1" class="ctrl-num" /></label>
            <label class="field"><span class="field-label">{{ T.outWidth }}</span>
              <select v-model.number="gp.output.width" class="ctrl-select">
                <option v-for="w in [1536, 2048, 2560, 3072, 3840]" :key="w" :value="w">{{ w }}×{{ w / 2 }}</option>
              </select></label>
            <label class="field"><span class="field-label">{{ T.panoSteps }}</span>
              <input v-model.number="gp.pano.steps" type="number" min="4" max="60" class="ctrl-num" /></label>
          </div>
          <details class="fold">
            <summary>{{ T.videoOptional }}</summary>
            <label class="check"><input v-model="gp.video.enabled" type="checkbox" /> {{ T.videoEnabled }}</label>
            <div v-if="gp.video.enabled" class="grid4">
              <label class="field"><span class="field-label">{{ T.duration }}</span>
                <input v-model.number="gp.output.duration_s" type="number" min="2" max="180" class="ctrl-num" /></label>
              <label class="field"><span class="field-label">{{ T.videoSteps }}</span>
                <input v-model.number="gp.video.steps" type="number" min="4" max="60" class="ctrl-num" /></label>
              <label class="field"><span class="field-label">{{ T.frames }}</span>
                <input v-model.number="gp.video.frames" type="number" min="17" max="241" step="4" class="ctrl-num" /></label>
            </div>
          </details>
          <div class="row">
            <button class="btn-primary" :disabled="gen.running || !gp.scene_ids.length" @click="startGen">{{ T.start }}</button>
            <button class="btn-ghost" :disabled="!gen.running" @click="stopGen">{{ T.stop }}</button>
            <span class="muted">{{ T.total }} {{ gp.scene_ids.length * gp.per_scene }} · {{ T.estimate }} {{ fmtSec(gp.scene_ids.length * gp.per_scene * (gp.video.enabled ? 240 : 75)) }}</span>
          </div>
          <p v-if="genErr" class="status-msg msg-error">{{ genErr }}</p>
        </section>
        <section v-if="gen.total" class="sec">
          <div class="row">
            <span>{{ T.done }} {{ gen.done }}/{{ gen.total }}</span>
            <span v-if="gen.failed" class="bad">{{ T.failed }} {{ gen.failed }}</span>
            <span v-if="gen.avg_pair_s" class="muted">{{ T.perItem }} {{ fmtSec(gen.avg_pair_s) }}</span>
            <span v-if="gen.running && gen.eta_s" class="muted">{{ T.eta }} {{ fmtSec(gen.eta_s) }}</span>
          </div>
          <div class="progress-bar"><div class="progress-fill" :style="{ width: genPct + '%' }"></div></div>
          <p v-if="gen.current" class="muted">#{{ gen.current.index }} {{ gen.current.scene }} · {{ T.stepNames[gen.current.step] || gen.current.step }}</p>
          <div class="log"><div v-for="l in [...gen.log].reverse()" :key="l.ts + l.msg">{{ fmtTime(l.ts) }} {{ l.msg }}</div></div>
        </section>
      </template>

      <!-- ═════════ ③ 选图 ═════════ -->
      <template v-if="tab === 'pick'">
        <p class="hint">{{ T.pickIntro }}</p>
        <div class="row">
          <label class="check"><input v-model="pickOnlyOpen" type="checkbox" /> {{ T.onlyUnselected }}</label>
          <button class="icon-btn" @click="refreshAll">↻</button>
        </div>
        <section v-for="s in pickScenes" :key="s.id" class="sec">
          <div class="row">
            <b>{{ T.scenePrefix }}{{ s.no }}「{{ s.name }}」</b><span class="tag">{{ s.condition }}</span>
            <span v-if="s.selected" class="good">✓ {{ T.selectedOk }}</span>
            <span v-else class="bad">{{ T.noneSelected }}</span>
          </div>
          <p v-if="!itemsByScene[s.id]?.length" class="muted small">{{ T.noCandidates }}</p>
          <div class="lib-grid">
            <div v-for="m in itemsByScene[s.id] || []" :key="m.pair_id" class="card"
                 :class="{ chosen: s.selected_pair === m.pair_id, rejected: m.qc === 'reject' }">
              <img v-if="m.urls.thumb" :src="m.urls.thumb" class="thumb" @click="panoSrc = m.urls.still" />
              <div class="card-body">
                <div class="row tight">
                  <button class="mini accent" :disabled="m.qc === 'reject'" @click="choose(s, m)">
                    {{ s.selected_pair === m.pair_id ? '✓ ' + T.chosen : T.choose }}</button>
                  <button class="mini" @click="panoSrc = m.urls.still">360°</button>
                  <button v-if="m.urls.video" class="mini" @click="videoSrc = m.urls.video">{{ T.video }}</button>
                  <button class="mini" @click="show(m.pair_id + ':image')">{{ T.toHeadset }}</button>
                </div>
                <div class="row tight">
                  <button class="mini" :class="{ 'q-reject on': m.qc === 'reject' }"
                          @click="qc(m, m.qc === 'reject' ? 'pending' : 'reject')">{{ m.qc === 'reject' ? T.unreject : T.reject }}</button>
                  <button class="mini danger" @click="removePair(m)">{{ T.delete }}</button>
                  <span class="muted small">{{ T.lum }} {{ fmt(m.features?.image?.luminance, 2) }}</span>
                </div>
              </div>
            </div>
          </div>
        </section>
      </template>

      <!-- ═════════ ④ 实验流程 ═════════ -->
      <template v-if="tab === 'proto'">
        <section class="sec">
          <p class="hint">{{ T.protoIntro }}</p>
          <div class="field">
            <div class="row"><span class="field-label">{{ T.protoScenes }}</span>
              <button class="mini" @click="pf.scene_ids = scenes.map((s) => s.id)">{{ T.all }}</button></div>
            <div class="preset-btns">
              <button v-for="s in scenes" :key="s.id" class="preset-btn" :class="{ active: pf.scene_ids.includes(s.id) }"
                      @click="toggle(pf.scene_ids, s.id)">{{ T.scenePrefix }}{{ s.no }} {{ s.name }}{{ s.selected ? '' : ' ⚠' }}</button>
            </div>
          </div>
          <div class="field">
            <div class="row"><span class="field-label">{{ T.catalogTitle }}</span>
              <button class="icon-btn" @click="loadCatalog">↻</button></div>
            <p class="hint">{{ T.catalogHint }}</p>
            <p v-if="!catalog.length" class="muted small">{{ T.catalogEmpty }}</p>
            <table v-else class="tbl">
              <thead><tr><th></th><th>ID</th><th>{{ T.catKind }}</th><th>{{ T.emotion }}</th><th>{{ T.condition }}</th>
                <th>{{ T.catEthics }}</th><th>QC</th><th>{{ T.catEnabled }}</th><th></th></tr></thead>
              <tbody>
                <tr v-for="c in catalog" :key="c.id">
                  <td><input type="checkbox" :checked="pf.extra_ids.includes(c.id)" @change="toggle(pf.extra_ids, c.id)" /></td>
                  <td class="mono" :title="c.name">{{ c.id }}</td>
                  <td>{{ T.kinds[c.kind] || c.kind }}</td><td>{{ c.target_emotion }}</td><td>{{ c.condition }}</td>
                  <td :class="c.ethics_tier >= 2 ? 'bad' : ''">{{ c.ethics_tier }}</td>
                  <td><select :value="c.qc" class="ctrl-select" @change="patchCat(c, { qc: $event.target.value })">
                    <option value="pending">pending</option><option value="ok">ok</option><option value="reject">reject</option>
                  </select></td>
                  <td><input type="checkbox" :checked="c.enabled" @change="patchCat(c, { enabled: $event.target.checked })" /></td>
                  <td class="bad small">{{ (c.problems || []).join('；') }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="grid4">
            <label class="field"><span class="field-label">{{ T.order }}</span>
              <select v-model="pf.order" class="ctrl-select">
                <option value="random">{{ T.orderRandom }}</option>
                <option value="fixed">{{ T.orderFixed }}</option>
              </select></label>
            <label class="field"><span class="field-label">{{ T.line }}</span>
              <select v-model="pf.line" class="ctrl-select">
                <option value="image">{{ T.image }}</option>
                <option value="video">{{ T.video }}</option>
              </select></label>
          </div>
          <div class="grid4">
            <label class="field"><span class="field-label">{{ T.rest }}</span>
              <input v-model.number="pf.rest_s" type="number" min="0" class="ctrl-num" /></label>
            <label class="field"><span class="field-label">{{ T.fixation }}</span>
              <input v-model.number="pf.fixation_s" type="number" min="0" class="ctrl-num" /></label>
            <label class="field"><span class="field-label">{{ T.stimulus }}</span>
              <input v-model.number="pf.stimulus_s" type="number" min="1" class="ctrl-num" /></label>
            <label class="field"><span class="field-label">{{ T.ratingTimeout }}</span>
              <input v-model.number="pf.rating_timeout_s" type="number" min="0" class="ctrl-num" /></label>
            <label class="field"><span class="field-label">{{ T.recovery }}</span>
              <input v-model.number="pf.recovery_s" type="number" min="0" class="ctrl-num" /></label>
          </div>
          <div class="sec-title small-title">{{ T.effTitle }}</div>
          <p class="hint">{{ T.effHint }}</p>
          <div class="grid4">
            <label class="field"><span class="field-label">{{ T.posedReps }}</span>
              <input v-model.number="pf.posed_reps" type="number" min="0" max="5" class="ctrl-num" /></label>
            <label class="field"><span class="field-label">{{ T.posedHold }}</span>
              <input v-model.number="pf.posed_hold_s" type="number" min="1" class="ctrl-num" /></label>
            <label class="field"><span class="field-label">{{ T.breakEvery }}</span>
              <input v-model.number="pf.break_every" type="number" min="0" class="ctrl-num" /></label>
            <label class="field"><span class="field-label">{{ T.breakS }}</span>
              <input v-model.number="pf.break_s" type="number" min="0" class="ctrl-num" /></label>
            <label class="field"><span class="field-label">{{ T.longRecovery }}</span>
              <input v-model.number="pf.long_recovery_arousal" type="number" min="0" max="9" class="ctrl-num" /></label>
            <label class="field"><span class="field-label">{{ T.minCov }}</span>
              <input v-model.number="pf.min_fea_coverage" type="number" min="0" max="1" step="0.05" class="ctrl-num" /></label>
          </div>
          <div class="row">
            <label><input v-model="pf.require_label7" type="checkbox" /> {{ T.requireLabel7 }}</label>
            <label><input v-model="pf.ask_intensity" type="checkbox" /> {{ T.askIntensity }}</label>
            <label><input v-model="pf.requeue_bad" type="checkbox" /> {{ T.requeueBad }}</label>
          </div>
          <div class="flow-line">
            <span class="step">{{ T.phases.rest }} {{ pf.rest_s }}s</span> →
            <template v-if="pf.posed_reps"><span class="step">{{ T.phases.posed }} {{ pf.posed_reps }}×7</span> →</template>
            <span class="step loop">[ {{ T.phases.fixation }} {{ pf.fixation_s }}s → {{ T.phases.stimulus }} {{ pf.stimulus_s }}s →
              {{ T.phases.rating }} → {{ T.phases.recovery }} {{ pf.recovery_s }}s ] × {{ pf.scene_ids.length + pf.extra_ids.length }}</span> →
            <span class="step">{{ T.phases.done }}</span>
          </div>
          <div class="row">
            <button class="btn-primary" @click="saveProto">{{ T.save }}</button>
            <span v-if="protoSaved" class="good">✓ {{ T.saved }}</span>
          </div>
          <p v-if="protoErr" class="status-msg msg-error">{{ protoErr }}</p>
        </section>
        <section class="sec">
          <div class="sec-title">{{ T.check }}</div>
          <template v-if="proto.problems?.length">
            <p v-for="(p, i) in proto.problems" :key="i" class="bad">✗ {{ p }}</p>
          </template>
          <p v-else class="good">✓ {{ T.ready }}：{{ proto.n_trials }} {{ T.trials }} · {{ T.estimate }} {{ fmtSec(proto.estimate_s || 0) }}</p>
        </section>
      </template>

      <!-- ═════════ ⑤ 运行实验 ═════════ -->
      <template v-if="tab === 'run'">
        <section class="sec">
          <div class="sec-title">{{ T.checklist }}</div>
          <div class="checks">
            <span :class="hs.presence?.online ? 'good' : 'bad'">{{ hs.presence?.online ? '✓' : '✗' }} {{ T.ckHeadset }}</span>
            <span :class="hs.fea_hz > 20 ? 'good' : 'bad'">{{ hs.fea_hz > 20 ? '✓' : '✗' }} {{ T.ckFea }}（{{ hs.fea_hz || 0 }} Hz）</span>
            <span :class="hs.clock_offset_ms != null ? 'good' : 'bad'">{{ hs.clock_offset_ms != null ? '✓' : '✗' }} {{ T.ckClock }}
              <template v-if="hs.clock_offset_ms != null">（{{ hs.clock_offset_ms }} ms）</template></span>
            <span :class="proto.problems?.length ? 'bad' : 'good'">{{ proto.problems?.length ? '✗' : '✓' }} {{ T.ckProto }}</span>
            <span :class="hs.prefetch_count && hs.cached_count >= hs.prefetch_count ? 'good' : 'warn'">
              {{ hs.prefetch_count && hs.cached_count >= hs.prefetch_count ? '✓' : '…' }} {{ T.ckCache }} {{ hs.cached_count || 0 }}/{{ hs.prefetch_count || 0 }}</span>
            <button class="mini" :disabled="busy || pb.mode === 'sequence'" @click="run(() => api.prefetch('all'))">{{ T.prefetch }}</button>
          </div>
          <p v-if="proto.problems?.length" class="muted small">{{ proto.problems.join('；') }}</p>
        </section>

        <section class="sec">
          <div class="grid4">
            <label class="field"><span class="field-label">{{ T.subject }}</span>
              <input v-model="subjectId" class="ctrl-num wide" placeholder="p01" /></label>
            <label class="field"><span class="field-label">{{ T.seed }}</span>
              <input v-model.number="runSeed" type="number" min="-1" class="ctrl-num" /></label>
          </div>
          <label class="row"><input v-model="consentTier2" type="checkbox" /> {{ T.consentTier2 }}</label>
          <div class="row">
            <button class="btn-primary big" :disabled="busy || pb.mode === 'sequence' || !subjectId || !!proto.problems?.length"
                    @click="startRun">{{ T.startRun }}</button>
            <button class="btn-ghost" :disabled="busy || pb.mode !== 'sequence'" @click="cmd(pb.paused ? 'resume' : 'pause')">
              {{ pb.paused ? T.resume : T.pause }}</button>
            <button class="btn-ghost" :disabled="busy || pb.mode !== 'sequence'" @click="run(api.seqSkip)">{{ T.skip }}</button>
            <button class="btn-ghost danger" :disabled="busy || pb.mode === 'idle'" @click="stopRun">{{ T.stopRun }}</button>
          </div>
          <p v-if="runErr" class="status-msg msg-error">{{ runErr }}</p>
        </section>

        <section v-if="pb.run" class="sec">
          <div class="row">
            <span class="tag big-tag">{{ T.phases[pb.phase] || pb.phase }}</span>
            <span v-if="pb.stimulus">{{ curScene }}</span>
            <span class="muted">{{ pb.run.subject_id }} · {{ T.trial }} {{ pb.run.index }}/{{ pb.run.total }}</span>
            <span v-if="pb.paused" class="tag warn">{{ T.paused }}</span>
            <span v-if="pb.recording" class="tag rec">● REC</span>
          </div>
          <div v-if="pb.phase_duration_s" class="progress-bar">
            <div class="progress-fill" :style="{ width: phasePct + '%' }"></div>
          </div>
          <p v-if="pb.waiting_headset" class="muted">{{ T.waitingHeadset }}</p>
          <p v-if="pb.phase === 'rating'" class="muted">{{ T.waitingRating }}</p>
          <p v-if="pb.cue" class="muted">{{ T.phases.posed }}：{{ pb.cue.emotion }}（{{ pb.cue.i }}/{{ pb.cue.n }}）</p>
          <p v-if="pb.phase === 'break'" class="muted">{{ T.breakHint }}</p>
          <p v-if="hs.error" class="bad small">headset: {{ hs.error }}</p>
          <table v-if="seqList.length" class="tbl">
            <thead><tr><th>{{ T.trial }}</th><th>{{ T.scene }}</th><th>{{ T.condition }}</th><th>{{ T.status }}</th>
              <th>{{ T.valence }}</th><th>{{ T.arousal }}</th><th>{{ T.emotion }}</th><th>FEA</th></tr></thead>
            <tbody>
              <tr v-for="it in seqList" :key="it.trial" :class="{ cur: it.trial === pb.run.index && pb.mode === 'sequence' }">
                <td>{{ it.trial }}</td><td>{{ it.scene_no ? `${T.scenePrefix}${it.scene_no} ` : '' }}{{ it.scene_name || it.stimulus_id }}</td>
                <td>{{ it.condition }}</td><td>{{ T.itemStatus[it.status] || it.status }}</td>
                <td>{{ it.rating?.valence ?? '' }}</td><td>{{ it.rating?.arousal ?? '' }}</td>
                <td>{{ it.rating?.label7 ?? '' }}{{ it.rating?.intensity ? ` ·${it.rating.intensity}` : '' }}</td>
                <td :class="it.quality === 'bad' ? 'bad' : ''">{{ it.fea_cov != null ? Math.round(it.fea_cov * 100) + '%' : '' }}
                  {{ it.attempt > 1 ? T.retry : '' }}{{ it.quality === 'bad' ? ' ✗' : '' }}</td>
              </tr>
            </tbody>
          </table>
        </section>

        <details class="sec fold">
          <summary>{{ T.manual }}</summary>
          <p class="hint">{{ T.manualHint }}</p>
          <div class="row">
            <button class="btn-ghost" :disabled="busy || pb.mode === 'sequence'" @click="cmd('blank')">{{ T.blank }}</button>
            <button class="btn-ghost" :disabled="busy || pb.mode !== 'manual' || !pb.stimulus" @click="cmd('rate')">{{ T.rate }}</button>
            <button class="btn-ghost" :disabled="busy || pb.mode !== 'manual'" @click="cmd(pb.paused ? 'resume' : 'pause')">{{ pb.paused ? T.resume : T.pause }}</button>
            <button class="btn-ghost danger" :disabled="busy || pb.mode !== 'manual'" @click="cmd('stop')">{{ T.stopRun }}</button>
          </div>
        </details>
      </template>

      <!-- ═════════ ⑥ 数据 ═════════ -->
      <template v-if="tab === 'data'">
        <section class="sec">
          <div class="row"><div class="sec-title">{{ T.sessions }}</div><button class="icon-btn" @click="loadData">↻</button></div>
          <p v-if="!sessions.length" class="muted">{{ T.noSessions }}</p>
          <table v-else class="tbl">
            <thead><tr><th>{{ T.subject }}</th><th>{{ T.session }}</th><th>{{ T.status }}</th><th>{{ T.rated }}</th>
              <th>FEA</th><th>{{ T.time }}</th><th></th></tr></thead>
            <tbody>
              <tr v-for="s in sessions" :key="s.session_id + s.subject_id">
                <td>{{ s.subject_id }}</td><td class="mono">{{ s.session_id }}</td>
                <td :class="s.status === 'done' ? 'good' : s.status === 'running' ? '' : 'bad'">{{ T.sessStatus[s.status] || s.status }}</td>
                <td>{{ s.n_rated }}/{{ s.n_trials }}<span v-if="s.n_bad" class="bad small"> ✗{{ s.n_bad }}</span></td>
                <td>{{ s.fea_rows }}</td>
                <td>{{ fmtTime(s.created_ms) }}</td>
                <td><button class="mini" @click="openSession(s)">{{ T.detail }}</button>
                  <button v-if="s.status !== 'done' && s.kind === 'protocol'" class="mini"
                          :disabled="busy || pb.mode === 'sequence'" @click="resumeRun(s)">{{ T.resumeRun }}</button></td>
              </tr>
            </tbody>
          </table>
          <template v-if="sessDetail">
            <div class="row"><b>{{ sessDetail.meta.subject_id }} / {{ sessDetail.meta.session_id }}</b>
              <span class="muted small mono">{{ sessDetail.meta.dir }}</span></div>
            <table class="tbl">
              <thead><tr><th>{{ T.trial }}</th><th>{{ T.scene }}</th><th>{{ T.valence }}</th><th>{{ T.arousal }}</th><th>{{ T.emotion }}</th><th>{{ T.ratingTime }}</th></tr></thead>
              <tbody>
                <tr v-for="r in sessDetail.ratings" :key="r.trial">
                  <td>{{ r.trial }}</td><td>{{ sceneName(r.scene_id) }}</td><td>{{ r.valence }}</td><td>{{ r.arousal }}</td>
                  <td>{{ r.label7 }}</td><td>{{ r.rating_ms ? (r.rating_ms / 1000).toFixed(1) + 's' : '' }}</td>
                </tr>
              </tbody>
            </table>
          </template>
        </section>

        <section class="sec">
          <div class="sec-title">{{ T.byScene }}</div>
          <p class="hint">{{ T.bySceneHint }}</p>
          <table class="tbl">
            <thead><tr><th>{{ T.scene }}</th><th>{{ T.condition }}</th><th>n</th><th>{{ T.valence }}</th><th>{{ T.arousal }}</th>
              <th>{{ T.hit }}</th><th>{{ T.feaExpr }}</th></tr></thead>
            <tbody>
              <tr v-for="s in scenes" :key="s.id">
                <td>{{ T.scenePrefix }}{{ s.no }} {{ s.name }}</td><td>{{ s.condition }}</td>
                <td>{{ cmp?.by_scene?.[s.id]?.n || 0 }}</td>
                <td>{{ fmt(cmp?.by_scene?.[s.id]?.valence, 1) }}</td><td>{{ fmt(cmp?.by_scene?.[s.id]?.arousal, 1) }}</td>
                <td :class="hitClass(cmp?.by_scene?.[s.id]?.hit)">{{ pct(cmp?.by_scene?.[s.id]?.hit) }}</td>
                <td>{{ fmt(cmp?.by_scene?.[s.id]?.fea_expr, 3) }}</td>
              </tr>
            </tbody>
          </table>
        </section>

        <section class="sec">
          <div class="sec-title">{{ T.toTrain }}</div>
          <p class="hint">{{ T.toTrainHint }}</p>
          <pre class="code">python DataSet/MyDataSet/prepare_timeline_dataset.py --root DataSet/MyDataSet/Capture --out DataSet/MyDataSet/timeline_sliced
python CoreCode/backend/train_timeline.py DataSet/MyDataSet/timeline_sliced --loso</pre>
        </section>
      </template>

      <!-- ═════════ ⑦ 进阶：图片线 vs 视频线 ═════════ -->
      <template v-if="tab === 'cmp'">
        <p class="hint">{{ T.cmpIntro }}</p>
        <section class="sec">
          <div class="sec-title">{{ T.cmpRun }}</div>
          <div class="grid4">
            <label class="field"><span class="field-label">{{ T.subject }}</span>
              <input v-model="subjectId" class="ctrl-num wide" /></label>
            <label class="field"><span class="field-label">{{ T.group }}</span>
              <select v-model="cmpGroup" class="ctrl-select">
                <option v-for="(label, k) in T.groupOpts" :key="k" :value="k">{{ label }}</option>
              </select></label>
          </div>
          <p class="hint">{{ T.groupHint }}</p>
          <div class="row">
            <button class="btn-primary" :disabled="busy || pb.mode === 'sequence' || !subjectId" @click="startCompare">{{ T.startRun }}</button>
            <span class="muted small">{{ T.cmpUsesProto }}</span>
          </div>
        </section>
        <section v-if="cmp" class="sec">
          <div class="row"><div class="sec-title">{{ T.verdict }}</div>
            <button class="icon-btn" @click="loadCompare">↻</button></div>
          <p v-for="(v, i) in cmp.verdict" :key="i" class="verdict">{{ v }}</p>
          <table class="tbl">
            <thead><tr><th>{{ T.metric }}</th><th>{{ T.image }}</th><th>{{ T.video }}</th><th>{{ T.diff }}</th><th>{{ T.pairs }}</th><th>p</th></tr></thead>
            <tbody>
              <tr v-for="m in ['hit', 'arousal', 'extremity', 'fea_expr']" :key="m">
                <td>{{ T.metrics[m] }}</td><td>{{ fmt(cmp.lines.image[m]) }}</td><td>{{ fmt(cmp.lines.video[m]) }}</td>
                <td>{{ fmtSigned(cmp.paired[m].mean_diff) }}</td><td>{{ cmp.paired[m].n_pairs }}</td>
                <td :class="{ good: cmp.paired[m].p != null && cmp.paired[m].p < 0.05 }">{{ fmt(cmp.paired[m].p) }}</td>
              </tr>
            </tbody>
          </table>
          <div class="sec-title">{{ T.lowlevel }}</div>
          <table class="tbl">
            <thead><tr><th></th><th>{{ T.lum }}</th><th>{{ T.contrast }}</th><th>colorfulness</th><th>{{ T.motion }}</th></tr></thead>
            <tbody>
              <tr v-for="line in ['image', 'video']" :key="line">
                <td>{{ line === 'image' ? T.image : T.video }}</td>
                <td v-for="k in ['luminance', 'contrast', 'colorfulness', 'motion']" :key="k">{{ fmt(cmp.lines[line].lowlevel?.[k], k === 'motion' ? 4 : 3) }}</td>
              </tr>
            </tbody>
          </table>
        </section>
      </template>
    </div>

    <div v-if="videoSrc" class="overlay" @click.self="videoSrc = null">
      <div class="video-box">
        <video :src="videoSrc" controls autoplay loop muted class="video"></video>
        <button class="icon-btn close" @click="videoSrc = null">✕</button>
      </div>
    </div>
    <PanoramaViewer v-if="panoSrc" :src="panoSrc" caption="" :enter-vr-label="'VR'" :download-label="'↓'" @close="panoSrc = null" />
  </div>
</template>

<script setup>
import { computed, defineAsyncComponent, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import * as api from '../api/stimlib.js'

const PanoramaViewer = defineAsyncComponent(() => import('./PanoramaViewer.vue'))

const props = defineProps({ locale: { type: Object, required: true } })
const T = computed(() => props.locale.stimlib)
const lang = computed(() => (props.locale.langSwitchPath === '/cn' ? 'ja' : 'zh'))

const TAB_KEY = 'stimlib.tab'
const tab = ref((() => { try { return localStorage.getItem(TAB_KEY) || 'scenes' } catch { return 'scenes' } })())
watch(tab, (t) => {
  try { localStorage.setItem(TAB_KEY, t) } catch { /* 隐私模式 */ }
  if (t === 'data' || t === 'cmp') loadData()
  if (t === 'proto' || t === 'run') loadProto()
  if (t === 'pick' || t === 'scenes') refreshAll()
})

const options = reactive({ conditions: [] })
const condLabel = (key) => options.conditions.find((c) => c.key === key)?.[lang.value] ?? key

const fmt = (v, d = 3) => (v == null ? '–' : Number(v).toFixed(d))
const fmtSigned = (v, d = 3) => (v == null ? '–' : (v >= 0 ? '+' : '') + Number(v).toFixed(d))
const pct = (v) => (v == null ? '–' : `${Math.round(v * 100)}%`)
const fmtSec = (s) => (s >= 3600 ? `${(s / 3600).toFixed(1)}h` : s >= 60 ? `${Math.round(s / 60)}min` : `${Math.round(s)}s`)
const fmtTime = (ms) => (ms ? new Date(ms).toLocaleString() : '')
const hitClass = (v) => (v == null ? '' : v >= 0.6 ? 'good' : v < 0.4 ? 'bad' : '')
function toggle(arr, v) { const i = arr.indexOf(v); if (i >= 0) arr.splice(i, 1); else arr.push(v) }

// ── ① 场景 ──
const scenes = ref([])
const templates = ref([])
const editId = ref(null)
const form = reactive({ name: '', condition: 'NEU', prompt: '', motion: '' })
const sceneErr = ref('')
const tplPick = ref('')
const sceneName = (id) => { const s = scenes.value.find((x) => x.id === id); return s ? `${T.value.scenePrefix}${s.no} ${s.name}` : id }

async function loadScenes() {
  try { const r = await api.listScenes(); scenes.value = r.scenes; templates.value = r.templates } catch { /* 后端未启动 */ }
}
function startEdit(s) { Object.assign(form, { name: s.name, condition: s.condition, prompt: s.prompt, motion: s.motion }); sceneErr.value = ''; editId.value = s.id }
function startNew() { Object.assign(form, { name: '', condition: 'NEU', prompt: '', motion: '' }); sceneErr.value = ''; editId.value = '__new__' }
async function saveEdit() {
  sceneErr.value = ''
  try {
    const r = editId.value === '__new__' ? await api.createScene({ ...form }) : await api.updateScene(editId.value, { ...form })
    scenes.value = r.scenes
    editId.value = null
  } catch (e) { sceneErr.value = e.message }
}
async function removeScene(s) {
  if (!window.confirm(T.value.deleteSceneConfirm)) return
  scenes.value = (await api.deleteScene(s.id)).scenes
}
async function move(i, d) {
  const ids = scenes.value.map((s) => s.id)
  ;[ids[i], ids[i + d]] = [ids[i + d], ids[i]]
  scenes.value = (await api.reorderScenes(ids)).scenes
}
async function addTemplate() {
  scenes.value = (await api.createScene({ template: tplPick.value })).scenes
  tplPick.value = ''
}

// ── ② 生成 ──
const gp = reactive({ scene_ids: [], per_scene: 3, seed: -1, pano: { steps: 20 },
  video: { enabled: false, steps: 20, frames: 121 }, output: { width: 2048, duration_s: 30 } })
const gen = reactive({ running: false, total: 0, done: 0, failed: 0, current: null, log: [], avg_pair_s: null, eta_s: null })
const genErr = ref('')
const genPct = computed(() => (gen.total ? Math.round(((gen.done + gen.failed) / gen.total) * 100) : 0))
async function loadGen() {
  try {
    const s = await api.genStatus()
    const changed = s.done !== gen.done || (gen.running && !s.running)
    Object.assign(gen, s)
    if (changed) refreshAll()
  } catch { /* 忽略 */ }
}
async function startGen() {
  genErr.value = ''
  try { Object.assign(gen, await api.genStart(JSON.parse(JSON.stringify(gp)))) } catch (e) { genErr.value = e.message }
}
async function stopGen() { try { Object.assign(gen, await api.genStop()) } catch (e) { genErr.value = e.message } }

// ── ③ 选图 ──
const items = ref([])
const pickOnlyOpen = ref(false)
const itemsByScene = computed(() => {
  const m = {}
  for (const it of items.value) (m[it.scene_id || ''] ||= []).push(it)
  return m
})
const pickScenes = computed(() => scenes.value.filter((s) => !pickOnlyOpen.value || !s.selected))
async function loadItems() { try { items.value = (await api.listItems()).items } catch { /* 忽略 */ } }
async function refreshAll() { await Promise.all([loadScenes(), loadItems()]) }
async function choose(s, m) {
  try { scenes.value = (await api.selectPair(s.id, s.selected_pair === m.pair_id ? '' : m.pair_id)).scenes } catch (e) { alert(e.message) }
}
async function qc(m, q) {
  try {
    const upd = await api.setQc(m.pair_id, q)
    items.value = items.value.map((x) => (x.pair_id === upd.pair_id ? upd : x))
  } catch (e) { alert(e.message) }
}
async function removePair(m) {
  if (!window.confirm(T.value.deleteConfirm)) return
  await api.deletePair(m.pair_id)
  await refreshAll()
}
const panoSrc = ref(null)
const videoSrc = ref(null)

// ── ④ 实验流程 ──
const pf = reactive({ scene_ids: [], order: 'random', line: 'image', rest_s: 60, fixation_s: 5, stimulus_s: 30,
  rating_timeout_s: 0, recovery_s: 10, posed_reps: 2, posed_hold_s: 4, posed_relax_s: 3, break_every: 0, break_s: 120,
  long_recovery_arousal: 7, recovery_long_s: 30, require_label7: true, ask_intensity: true, min_fea_coverage: 0.8,
  requeue_bad: true, max_attempts: 2, extra_ids: [], templates: {} })
const catalog = ref([])
async function loadCatalog() {
  try { catalog.value = (await api.listCatalog()).items || [] } catch { /* 忽略 */ }
}
async function patchCat(c, body) {
  try {
    await api.patchCatalog(c.id, body)
    await loadCatalog()
    await loadProto()
  } catch (e) { protoErr.value = e.message }
}
const proto = reactive({ problems: [], n_trials: 0, estimate_s: 0 })
const protoErr = ref('')
const protoSaved = ref(false)
async function loadProto() {
  try {
    const r = await api.getProtocol()
    Object.assign(pf, r.protocol)
    if (!pf.scene_ids.length) pf.scene_ids = scenes.value.map((s) => s.id)
    Object.assign(proto, { problems: r.problems, n_trials: r.n_trials, estimate_s: r.estimate_s })
  } catch { /* 忽略 */ }
}
async function saveProto() {
  protoErr.value = ''
  try {
    const r = await api.saveProtocol(JSON.parse(JSON.stringify(pf)))
    Object.assign(proto, { problems: r.problems, n_trials: r.n_trials, estimate_s: r.estimate_s })
    protoSaved.value = true
    setTimeout(() => { protoSaved.value = false }, 2000)
  } catch (e) { protoErr.value = e.message }
}

// ── ⑤ 运行 ──
const pb = reactive({ mode: 'idle', phase: 'idle', stimulus: null, paused: false, run: null,
  phase_duration_s: null, phase_elapsed_s: 0, waiting_headset: false, recording: false })
const hs = reactive({ presence: null, fea_hz: 0, clock_offset_ms: null, cached_count: 0, prefetch_count: 0, error: '' })
const busy = ref(false)
const runErr = ref('')
const subjectId = ref('')
const runSeed = ref(-1)
const consentTier2 = ref(false)
const seqList = ref([])
const phasePct = computed(() => (pb.phase_duration_s ? Math.min(100, Math.round((pb.phase_elapsed_s / pb.phase_duration_s) * 100)) : 0))
const curScene = computed(() => {
  const it = seqList.value.find((x) => x.trial === pb.run?.index)
  return it ? `${it.scene_no ? T.value.scenePrefix + it.scene_no + ' ' : ''}${it.scene_name || it.stimulus_id}` : ''
})

async function pollPlayback() {
  try {
    Object.assign(pb, await api.playbackState())
    Object.assign(hs, await api.headsetStatus())
    if (pb.mode === 'sequence' || (pb.run && !seqList.value.length)) seqList.value = (await api.seqItems()).items
  } catch { /* 忽略 */ }
}
async function run(fn) {
  busy.value = true
  runErr.value = ''
  try {
    const s = await fn()
    if (s && 'phase' in s) Object.assign(pb, s)
  } catch (e) { runErr.value = e.message } finally { busy.value = false }
}
const cmd = (c, extra = {}) => run(() => api.playbackCmd(c, extra))
async function startRun() {
  await run(() => api.seqStart({ kind: 'protocol', subject_id: subjectId.value.trim(), seed: runSeed.value,
    consent_tier2: consentTier2.value }))
  seqList.value = (await api.seqItems()).items
}
async function resumeRun(s) {
  if (!window.confirm(T.value.resumeConfirm.replace('{s}', `${s.subject_id}/${s.session_id}`))) return
  subjectId.value = s.subject_id
  await run(() => api.seqStart({ kind: 'protocol', subject_id: s.subject_id, seed: s.seed ?? -1,
    resume_session: s.session_id, consent_tier2: consentTier2.value }))
  seqList.value = (await api.seqItems()).items
  tab.value = 'run'
}
async function stopRun() {
  if (pb.mode === 'sequence' && !window.confirm(T.value.stopConfirm)) return
  await cmd('stop')
}
async function show(stimId) {
  try { Object.assign(pb, await api.playbackCmd('show', { stimulus_id: stimId })) } catch (e) { alert(e.message) }
}

// ── ⑥ 数据 / ⑦ 对比 ──
const sessions = ref([])
const sessDetail = ref(null)
const cmp = ref(null)
const cmpGroup = ref('A')
async function loadCompare() { try { cmp.value = await api.compareLines() } catch { /* 忽略 */ } }
async function loadData() {
  try { sessions.value = (await api.listSessions()).sessions } catch { /* 忽略 */ }
  loadCompare()
}
async function openSession(s) { sessDetail.value = await api.sessionDetail(s.subject_id, s.session_id) }
async function startCompare() {
  const p = pf
  await run(() => api.seqStart({ kind: 'compare', subject_id: subjectId.value.trim(), cb_group: cmpGroup.value,
    fixation_s: p.fixation_s, stimulus_s: p.stimulus_s, rating_timeout_s: p.rating_timeout_s,
    recovery_s: p.recovery_s, rest_s: p.rest_s }))
  tab.value = 'run'
}

// ── 生命周期 ──
let genTimer = null
let pbTimer = null
onMounted(async () => {
  try { options.conditions = (await api.getOptions()).conditions } catch { /* 忽略 */ }
  await refreshAll()
  gp.scene_ids = scenes.value.map((s) => s.id)
  await loadProto()
  loadCatalog()
  loadGen()
  pollPlayback()
  if (tab.value === 'data' || tab.value === 'cmp') loadData()
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
.header-right { display: flex; gap: 12px; }
.panel-title { font-weight: 700; font-size: 0.95rem; }
.tabs { display: flex; gap: 4px; flex-wrap: wrap; }
.tab-btn {
  padding: 5px 12px; border-radius: 6px; border: 1px solid var(--color-border);
  background: transparent; color: var(--color-text-muted); font-size: 0.78rem; cursor: pointer;
}
.tab-btn.active { border-color: #2563eb; color: #fff; background: #2563eb; }
.tab-btn.adv { border-style: dashed; }
.conn-badge { display: inline-flex; align-items: center; gap: 6px; font-size: 0.75rem; color: var(--color-text-muted); }
.conn-dot { width: 8px; height: 8px; border-radius: 50%; background: #666; }
.conn-ok .conn-dot { background: #2ecc71; }
.conn-off .conn-dot { background: #ff6b6b; }

.body { flex: 1; min-height: 0; overflow-y: auto; padding: 14px 16px 24px; display: flex; flex-direction: column; gap: 10px; }
.sec {
  border: 1px solid var(--color-border); border-radius: var(--radius); background: var(--color-surface-2);
  padding: 10px 12px; display: flex; flex-direction: column; gap: 8px;
}
.sec-title { font-size: 0.8rem; font-weight: 700; letter-spacing: 0.04em; }
.hint { font-size: 0.76rem; color: var(--color-text-muted); line-height: 1.6; }
.muted { color: var(--color-text-muted); font-size: 0.78rem; }
.small { font-size: 0.7rem; }
.bad { color: #ff6b6b; font-size: 0.8rem; }
.good { color: #2ecc71; font-size: 0.8rem; }
.warn { color: #f0b43c; font-size: 0.8rem; }
.mono { font-family: Consolas, monospace; font-size: 0.72rem; }
.nowrap { white-space: nowrap; }
.row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.row.tight { gap: 4px; }
.grid4 { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 8px; align-items: end; }
.field { display: flex; flex-direction: column; gap: 4px; }
.field-label { font-size: 0.72rem; font-weight: 600; color: var(--color-text-muted); }
.check { display: inline-flex; align-items: center; gap: 6px; font-size: 0.78rem; color: var(--color-text-muted); }
.fold > summary { cursor: pointer; font-size: 0.78rem; color: var(--color-text-muted); }
.checks { display: flex; flex-wrap: wrap; gap: 14px; align-items: center; }

.ctrl-num, .ctrl-select, .ctrl-area {
  background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 8px;
  color: var(--color-text); font-size: 0.82rem; padding: 6px 9px; outline: none; width: 100%; font-family: inherit;
}
.ctrl-area { resize: vertical; line-height: 1.5; }
.tpl-select { width: auto; min-width: 220px; }
.preset-btns { display: flex; gap: 5px; flex-wrap: wrap; }
.preset-btn {
  padding: 4px 10px; border-radius: 6px; border: 1px solid var(--color-border); background: transparent;
  color: var(--color-text-muted); font-size: 0.74rem; cursor: pointer;
}
.preset-btn.active { border-color: #2563eb; color: #8fb0ff; background: rgba(37, 99, 235, 0.12); }
.btn-primary {
  padding: 8px 18px; border: none; border-radius: 8px; background: #2563eb; color: #fff;
  font-size: 0.85rem; font-weight: 700; cursor: pointer;
}
.btn-primary.big { padding: 10px 28px; font-size: 0.95rem; }
.btn-ghost {
  padding: 7px 14px; border: 1px solid var(--color-border); border-radius: 8px; background: transparent;
  color: var(--color-text); font-size: 0.8rem; cursor: pointer;
}
.btn-primary:disabled, .btn-ghost:disabled, .mini:disabled { opacity: 0.4; cursor: not-allowed; }
.btn-ghost.danger, .mini.danger { color: #ff6b6b; border-color: rgba(255, 107, 107, 0.4); }
.icon-btn { background: transparent; border: 1px solid var(--color-border); border-radius: 6px; color: var(--color-text); cursor: pointer; padding: 2px 8px; }

.progress-bar { height: 6px; border-radius: 3px; background: var(--color-border); overflow: hidden; }
.progress-fill { height: 100%; background: #2563eb; transition: width 0.3s ease; }
.status-msg { font-size: 0.78rem; border-radius: 6px; padding: 7px 10px; }
.msg-error { background: rgba(255, 107, 107, 0.12); color: #ff6b6b; }
.log {
  max-height: 180px; overflow-y: auto; font-family: Consolas, monospace; font-size: 0.7rem;
  color: var(--color-text-muted); background: var(--color-surface); border-radius: 6px; padding: 6px 8px;
}
.code {
  font-family: Consolas, monospace; font-size: 0.74rem; background: var(--color-surface); border-radius: 6px;
  padding: 8px 10px; white-space: pre-wrap; color: var(--color-text);
}

.tbl { width: 100%; border-collapse: collapse; font-size: 0.78rem; }
.tbl th, .tbl td { padding: 6px 8px; border-bottom: 1px solid var(--color-border); text-align: left; vertical-align: top; }
.tbl th { color: var(--color-text-muted); font-weight: 600; }
.tbl tr.cur td { background: rgba(37, 99, 235, 0.15); }
.tbl tr.editing td { background: rgba(37, 99, 235, 0.06); }
.prompt-cell { max-width: 420px; color: var(--color-text-muted); font-size: 0.74rem; }
.mini-thumb { width: 120px; aspect-ratio: 2 / 1; object-fit: cover; border-radius: 4px; cursor: zoom-in; }
.edit-form { display: flex; flex-direction: column; gap: 8px; }
.flow-line { font-size: 0.78rem; color: var(--color-text-muted); line-height: 1.8; }
.flow-line .step { color: var(--color-text); }
.flow-line .loop { color: #8fb0ff; }

.lib-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 10px; }
.card { border: 2px solid var(--color-border); border-radius: var(--radius); background: var(--color-surface); overflow: hidden; }
.card.chosen { border-color: #2ecc71; }
.card.rejected { opacity: 0.45; }
.thumb { width: 100%; aspect-ratio: 2 / 1; object-fit: cover; display: block; cursor: zoom-in; }
.card-body { padding: 7px; display: flex; flex-direction: column; gap: 5px; }
.tag { font-size: 0.7rem; padding: 1px 7px; border-radius: 4px; background: rgba(37, 99, 235, 0.18); color: #8fb0ff; }
.tag.warn { background: rgba(240, 180, 60, 0.18); color: #f0b43c; }
.tag.rec { background: rgba(255, 70, 70, 0.2); color: #ff6b6b; }
.big-tag { font-size: 0.85rem; padding: 3px 10px; }
.mini {
  padding: 3px 8px; border-radius: 5px; border: 1px solid var(--color-border); background: transparent;
  color: var(--color-text-muted); font-size: 0.72rem; cursor: pointer;
}
.mini.accent { border-color: #2563eb; color: #8fb0ff; }
.mini.on.q-reject { background: rgba(255, 107, 107, 0.18); color: #ff6b6b; border-color: #ff6b6b; }
.verdict { font-size: 0.82rem; line-height: 1.6; }

.overlay { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.8); display: flex; align-items: center; justify-content: center; z-index: 50; }
.video-box { position: relative; width: min(1400px, 94vw); }
.video { width: 100%; border-radius: 8px; background: #000; }
.close { position: absolute; top: 8px; right: 8px; }
</style>
