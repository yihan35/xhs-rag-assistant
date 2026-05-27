import { useState, useCallback, useRef, useEffect } from 'react'

const BASE = ''  // 通过 Vite proxy 转发到 :8000

export function useNotes(userId) {
  const [notes, setNotes]     = useState([])
  const [loading, setLoading] = useState(false)
  const [stats, setStats]     = useState(null)

  const fetchNotes = useCallback(async (uid = userId) => {
    if (!uid) return
    setLoading(true)
    try {
      const [notesRes, statsRes] = await Promise.all([
        fetch(`${BASE}/api/notes?user_id=${encodeURIComponent(uid)}&page_size=100`),
        fetch(`${BASE}/api/stats`),
      ])
      if (notesRes.ok) {
        const data = await notesRes.json()
        setNotes(data.notes || [])
      }
      if (statsRes.ok) {
        setStats(await statsRes.json())
      }
    } catch (e) {
      console.error('fetchNotes error:', e)
    } finally {
      setLoading(false)
    }
  }, [userId])

  return { notes, loading, stats, fetchNotes }
}

/**
 * 收藏夹同步 hook
 *
 * state: 'idle' | 'running' | 'done' | 'error'
 * startSync(userId) — 触发同步；同步完成后自动回调 onDone
 */
export function useSync(onDone) {
  const [state,    setState]    = useState('idle')
  const [errorMsg, setErrorMsg] = useState('')
  const pollRef = useRef(null)

  // 清理定时器
  useEffect(() => () => clearInterval(pollRef.current), [])

  const startSync = useCallback(async (userId = '') => {
    if (state === 'running') return
    setState('running')
    setErrorMsg('')

    try {
      const res = await fetch('/api/sync', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ user_id: userId }),
      })
      // 409 = 已在运行中，也视作 running
      if (!res.ok && res.status !== 409) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || `启动失败 (${res.status})`)
      }

      // 每 2 秒轮询状态
      pollRef.current = setInterval(async () => {
        try {
          const sr   = await fetch('/api/sync/status')
          const data = await sr.json()
          if (!data.running) {
            clearInterval(pollRef.current)
            if (data.error) {
              setErrorMsg(data.error)
              setState('error')
              setTimeout(() => setState('idle'), 3000)
            } else {
              setState('done')
              onDone?.()
              setTimeout(() => setState('idle'), 2000)
            }
          }
        } catch {
          clearInterval(pollRef.current)
          setErrorMsg('网络错误，无法获取同步状态')
          setState('error')
          setTimeout(() => setState('idle'), 3000)
        }
      }, 2000)
    } catch (e) {
      setErrorMsg(e.message)
      setState('error')
      setTimeout(() => setState('idle'), 3000)
    }
  }, [state, onDone])

  return { startSync, syncState: state, syncError: errorMsg }
}

export function useFavoriteUpdates(userId, { intervalMs = 60000, onNewUpdates } = {}) {
  const [updates, setUpdates] = useState([])
  const [loading, setLoading] = useState(false)
  const notifiedRef = useRef(new Set())

  const fetchUpdates = useCallback(async (uid = userId) => {
    if (!uid) return []
    setLoading(true)
    try {
      const res = await fetch(`${BASE}/api/updates?user_id=${encodeURIComponent(uid)}`)
      if (!res.ok) return []
      const data = await res.json()
      const nextUpdates = data.notes || []
      const newItems = nextUpdates.filter(note => {
        const version = note.content_hash || note.content_changed_at || ''
        const key = `${note.note_id}:${version}`
        if (notifiedRef.current.has(key)) return false
        notifiedRef.current.add(key)
        return true
      })
      if (newItems.length > 0) onNewUpdates?.(newItems)
      setUpdates(nextUpdates)
      return nextUpdates
    } catch (e) {
      console.error('fetchFavoriteUpdates error:', e)
      return []
    } finally {
      setLoading(false)
    }
  }, [userId, onNewUpdates])

  const markSeen = useCallback(async (noteId = null, uid = userId) => {
    if (!uid) return
    try {
      const res = await fetch(`${BASE}/api/updates/seen`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: uid, note_id: noteId }),
      })
      if (!res.ok) return
      setUpdates(current => (
        noteId ? current.filter(note => note.note_id !== noteId) : []
      ))
    } catch (e) {
      console.error('markFavoriteUpdateSeen error:', e)
    }
  }, [userId])

  useEffect(() => {
    if (!userId) return undefined
    fetchUpdates(userId)
    const timer = setInterval(() => fetchUpdates(userId), intervalMs)
    return () => clearInterval(timer)
  }, [userId, intervalMs, fetchUpdates])

  return {
    updates,
    updatedNoteIds: new Set(updates.map(note => note.note_id)),
    updateCount: updates.length,
    loading,
    fetchUpdates,
    markSeen,
  }
}

/** search 模式：JSON 响应 */
export async function queryApi({ query, userId, mode, sessionId, topK = 6 }) {
  const res = await fetch(`${BASE}/api/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query,
      user_id: userId,
      mode,
      top_k: topK,
      session_id: sessionId,
    }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `请求失败 (${res.status})`)
  }
  return res.json()
}

/**
 * analysis 模式：SSE 流式响应
 *
 * @param {{ query, userId, sessionId, topK }} params
 * @param {{ onSources, onChunk, onDone, onError }} callbacks
 * @param {AbortSignal} [signal]  传入 AbortController.signal 可中止
 */
export async function queryStreamApi({ query, userId, sessionId, topK = 6 }, callbacks = {}, signal) {
  const res = await fetch(`${BASE}/api/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query,
      user_id: userId,
      mode: 'analysis',
      top_k: topK,
      session_id: sessionId,
    }),
    signal,
  })

  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `请求失败 (${res.status})`)
  }

  const reader  = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer    = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      const parts = buffer.split('\n\n')
      buffer = parts.pop()  // 末尾不完整块留下次

      for (const part of parts) {
        const line = part.trim()
        if (!line.startsWith('data: ')) continue
        let evt
        try { evt = JSON.parse(line.slice(6)) } catch { continue }

        if      (evt.type === 'sources') callbacks.onSources?.(evt)
        else if (evt.type === 'chunk')   callbacks.onChunk?.(evt.content)
        else if (evt.type === 'done')    callbacks.onDone?.()
        else if (evt.type === 'error')   callbacks.onError?.(new Error(evt.message))
      }
    }
  } catch (err) {
    if (err.name === 'AbortError') {
      // 用户主动中止，视为正常结束
      callbacks.onDone?.()
    } else {
      throw err
    }
  } finally {
    reader.releaseLock()
  }
}
