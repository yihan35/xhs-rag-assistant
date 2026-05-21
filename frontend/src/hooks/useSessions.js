import { useState, useCallback, useEffect } from 'react'

const LS_KEY = 'xhs_sessions'
const MAX_SESSIONS = 30

function loadSessions() {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

/**
 * 会话管理 hook
 *
 * 每个 session 结构：
 * {
 *   id: string,
 *   title: string,        // 第一条用户消息，超长截断
 *   createdAt: string,    // ISO 时间
 *   messages: Message[],
 * }
 *
 * 每条 message 结构：
 * {
 *   id: string,
 *   role: 'user' | 'assistant',
 *   content: string,
 *   sources: [],
 *   mode: 'search' | 'analysis',
 *   status: 'searching' | 'found' | 'streaming' | 'done' | 'empty' | 'error',
 * }
 */
export function useSessions() {
  const [sessions, setSessions] = useState(loadSessions)
  const [currentId, setCurrentId] = useState(() => {
    const saved = loadSessions()
    return saved.length > 0 ? saved[0].id : null
  })

  // 持久化到 localStorage
  useEffect(() => {
    localStorage.setItem(LS_KEY, JSON.stringify(sessions.slice(0, MAX_SESSIONS)))
  }, [sessions])

  const currentSession = sessions.find(s => s.id === currentId) ?? null

  const createSession = useCallback(() => {
    const id = crypto.randomUUID()
    const newSession = {
      id,
      title: '新会话',
      createdAt: new Date().toISOString(),
      messages: [],
    }
    setSessions(prev => [newSession, ...prev].slice(0, MAX_SESSIONS))
    setCurrentId(id)
    return id
  }, [])

  const deleteSession = useCallback((id) => {
    setSessions(prev => {
      const next = prev.filter(s => s.id !== id)
      // 同步更新 currentId：如果删的是当前，选下一个
      setCurrentId(cur => {
        if (cur !== id) return cur
        const remaining = next
        if (remaining.length > 0) return remaining[0].id
        // 没有剩余，创建一个新的（在下一个 effect 里处理）
        return null
      })
      return next
    })
  }, [])

  const selectSession = useCallback((id) => {
    setCurrentId(id)
  }, [])

  // 用函数式 updater 更新 session 内部字段（避免闭包过期）
  const updateSession = useCallback((id, updater) => {
    setSessions(prev => prev.map(s => s.id === id ? updater(s) : s))
  }, [])

  // 没有会话时自动创建一个
  useEffect(() => {
    if (sessions.length === 0) {
      const id = crypto.randomUUID()
      const newSession = {
        id,
        title: '新会话',
        createdAt: new Date().toISOString(),
        messages: [],
      }
      setSessions([newSession])
      setCurrentId(id)
    }
  }, [sessions.length])

  // currentId 为 null 但还有 session 时，选最新一条
  useEffect(() => {
    if (currentId === null && sessions.length > 0) {
      setCurrentId(sessions[0].id)
    }
  }, [currentId, sessions])

  return {
    sessions,
    currentId,
    currentSession,
    createSession,
    deleteSession,
    selectSession,
    updateSession,
  }
}
