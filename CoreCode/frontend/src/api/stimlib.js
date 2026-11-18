// 图片刺激实验流程：场景预设 → 生成 → 选图 → 实验流程 → 运行 → 数据（后端 use_stimlib）。

async function call(path, { method = 'GET', body } = {}) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  let data = null
  try { data = await res.json() } catch { /* 空响应 */ }
  if (!res.ok) throw new Error(data?.error || data?.detail || `[${res.status}]`)
  return data
}

const enc = encodeURIComponent

export const getOptions = () => call('/api/stimlib/options')

// ① 场景预设
export const listScenes = () => call('/api/stimlib/scenes')
export const createScene = (body) => call('/api/stimlib/scenes', { method: 'POST', body })
export const updateScene = (id, body) => call(`/api/stimlib/scenes/${enc(id)}`, { method: 'PUT', body })
export const deleteScene = (id) => call(`/api/stimlib/scenes/${enc(id)}`, { method: 'DELETE' })
export const reorderScenes = (ids) => call('/api/stimlib/scenes/reorder', { method: 'POST', body: { ids } })
export const selectPair = (id, pairId) =>
  call(`/api/stimlib/scenes/${enc(id)}/select`, { method: 'POST', body: { pair_id: pairId } })

// ② 生成 / ③ 刺激库
export const listItems = () => call('/api/stimlib/items')
export const setQc = (pairId, qc, note = '') =>
  call(`/api/stimlib/items/${enc(pairId)}`, { method: 'PATCH', body: { qc, note } })
export const deletePair = (pairId) => call(`/api/stimlib/items/${enc(pairId)}`, { method: 'DELETE' })
export const genStatus = () => call('/api/stimlib/generate/status')
export const genStart = (params) => call('/api/stimlib/generate', { method: 'POST', body: params })
export const genStop = () => call('/api/stimlib/generate/stop', { method: 'POST' })

// ④ 实验流程
export const getProtocol = () => call('/api/stimlib/protocol')
export const saveProtocol = (p) => call('/api/stimlib/protocol', { method: 'PUT', body: p })

// 统一刺激清单（事件场景 / 模仿片段 / 带常模视频）
export const listCatalog = () => call('/api/stimlib/catalog')
export const patchCatalog = (id, body) => call(`/api/stimlib/catalog/${enc(id)}`, { method: 'PATCH', body })

// ⑤ 运行
export const playbackState = () => call('/api/stimlib/playback')
export const headsetStatus = () => call('/api/stimlib/playback/headset')
export const playbackCmd = (cmd, extra = {}) =>
  call('/api/stimlib/playback/cmd', { method: 'POST', body: { cmd, ...extra } })
export const prefetch = (ids) => call('/api/stimlib/playback/prefetch', { method: 'POST', body: { ids } })
export const seqStart = (params) => call('/api/stimlib/sequence/start', { method: 'POST', body: params })
export const seqSkip = () => call('/api/stimlib/sequence/skip', { method: 'POST' })
export const seqItems = () => call('/api/stimlib/sequence/items')

// ⑥ 数据
export const listSessions = () => call('/api/stimlib/sessions')
export const sessionDetail = (subject, session) => call(`/api/stimlib/sessions/${enc(subject)}/${enc(session)}`)
export const compareLines = (includeManual = false) => call(`/api/stimlib/compare?include_manual=${includeManual}`)
