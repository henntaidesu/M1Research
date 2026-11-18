// 刺激库：图片线 / 视频线成对生成、头显播放控制、两条线对比（后端 use_stimlib）。

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

export const getOptions = () => call('/api/stimlib/options')
export const listItems = (condition = '') =>
  call(`/api/stimlib/items${condition ? `?condition=${encodeURIComponent(condition)}` : ''}`)
export const setQc = (pairId, qc, note = '') =>
  call(`/api/stimlib/items/${encodeURIComponent(pairId)}`, { method: 'PATCH', body: { qc, note } })
export const deletePair = (pairId) =>
  call(`/api/stimlib/items/${encodeURIComponent(pairId)}`, { method: 'DELETE' })

export const genStatus = () => call('/api/stimlib/generate/status')
export const genStart = (params) => call('/api/stimlib/generate', { method: 'POST', body: params })
export const genStop = () => call('/api/stimlib/generate/stop', { method: 'POST' })

export const playbackState = () => call('/api/stimlib/playback')
export const headsetStatus = () => call('/api/stimlib/playback/headset')
export const playbackCmd = (cmd, extra = {}) =>
  call('/api/stimlib/playback/cmd', { method: 'POST', body: { cmd, ...extra } })
export const prefetch = (ids) => call('/api/stimlib/playback/prefetch', { method: 'POST', body: { ids } })

export const seqStart = (params) => call('/api/stimlib/sequence/start', { method: 'POST', body: params })
export const seqSkip = () => call('/api/stimlib/sequence/skip', { method: 'POST' })
export const seqItems = () => call('/api/stimlib/sequence/items')
export const listRuns = () => call('/api/stimlib/runs')

export const compareLines = (includeManual = false) =>
  call(`/api/stimlib/compare?include_manual=${includeManual}`)
