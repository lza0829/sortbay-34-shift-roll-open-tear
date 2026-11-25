export async function api(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  const text = await res.text()
  let data
  try { data = text ? JSON.parse(text) : null } catch { data = { 明细: text } }
  if (!res.ok) {
    const msg = (data && (data.detail || data.明细)) || res.statusText
    const error = new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
    error.status = res.status
    error.data = data
    throw error
  }
  return data
}

export function emitBoardRefresh(detail = {}) {
  window.dispatchEvent(new CustomEvent('sortbay:refresh', { detail }))
}

export function onBoardRefresh(handler) {
  const listener = () => handler()
  window.addEventListener('sortbay:refresh', listener)
  return () => window.removeEventListener('sortbay:refresh', listener)
}
