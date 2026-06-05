import { useState, useRef, useEffect, useCallback } from 'react'
import { Send, Search, Sparkles, Square, Lightbulb } from 'lucide-react'
import MessageBubble from './MessageBubble.jsx'
import { queryApi, queryStreamApi } from '../hooks/useApi.js'

export default function ChatArea({ session, onUpdateSession, userId, noteCount }) {
  const [input, setInput]   = useState('')
  const [mode, setMode]     = useState('search')
  const [loading, setLoading] = useState(false)
  const [autoScroll, setAutoScroll] = useState(true)
  const [suggestions, setSuggestions] = useState(null)
  const [suggestionsLoading, setSuggestionsLoading] = useState(false)

  const scrollRef   = useRef(null)
  const textareaRef = useRef(null)
  const abortRef    = useRef(null)
  const bottomRef   = useRef(null)

  const messages = session?.messages ?? []

  // 自动滚到底部（仅当 autoScroll 开启时）
  useEffect(() => {
    if (autoScroll && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages, autoScroll])

  // 检测用户手动上滚
  const handleScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    setAutoScroll(atBottom)
  }, [])

  // 自动调整 textarea 高度
  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, 120) + 'px'
  }, [input])

  // 加载个性化建议问题
  useEffect(() => {
    if (!userId || noteCount === null || noteCount === undefined) return
    setSuggestionsLoading(true)
    fetch(`/api/suggestions?user_id=${encodeURIComponent(userId)}`)
      .then(res => res.ok ? res.json() : Promise.reject(res))
      .then(data => setSuggestions(data.suggestions || []))
      .catch(() => setSuggestions([]))
      .finally(() => setSuggestionsLoading(false))
  }, [userId, noteCount])

  const stopStream = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const sendMessage = useCallback(async (query = input.trim()) => {
    if (!query || loading || !userId || !session) return
    setInput('')

    const isFirst = messages.length === 0
    const userMsgId = crypto.randomUUID()
    const aiMsgId   = crypto.randomUUID()

    const userMsg = { id: userMsgId, role: 'user', content: query }
    const aiMsg   = {
      id: aiMsgId,
      role: 'assistant',
      content: '',
      sources: [],
      mode,
      status: 'searching',
    }

    onUpdateSession(s => ({
      ...s,
      title: isFirst ? query.slice(0, 40) : s.title,
      messages: [...s.messages, userMsg, aiMsg],
    }))

    setLoading(true)
    setAutoScroll(true)
    abortRef.current = new AbortController()

    const updateAi = (updater) => {
      onUpdateSession(s => ({
        ...s,
        messages: s.messages.map(m => m.id === aiMsgId ? updater(m) : m),
      }))
    }

    try {
      if (mode === 'analysis') {
        await queryStreamApi(
          { query, userId, sessionId: session.id },
          {
            onSources: ({ sources }) => {
              updateAi(m => ({
                ...m,
                sources: sources ?? [],
                status: sources?.length ? 'found' : 'empty',
              }))
            },
            onChunk: (text) => {
              updateAi(m => ({
                ...m,
                content: (m.content || '') + text,
                status: 'streaming',
              }))
            },
            onDone: () => {
              updateAi(m => ({ ...m, status: 'done' }))
            },
            onError: (err) => {
              updateAi(m => ({
                ...m,
                content: `出错了：${err.message}`,
                status: 'error',
              }))
            },
          },
          abortRef.current.signal,
        )
      } else {
        // search 模式：JSON 响应
        const result = await queryApi({ query, userId, mode, sessionId: session.id })
        updateAi(m => ({
          ...m,
          sources: result.sources ?? [],
          status: result.sources?.length ? 'done' : 'empty',
        }))
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        updateAi(m => ({
          ...m,
          content: `出错了：${err.message}`,
          status: 'error',
        }))
      }
    } finally {
      setLoading(false)
      abortRef.current = null
    }
  }, [input, loading, userId, session, mode, messages.length, onUpdateSession])

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  const isEmpty = messages.length === 0

  return (
    <div className="flex flex-col h-full">
      {/* 消息区 */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto px-6 py-6 space-y-5"
      >
        {isEmpty ? (
          <WelcomeScreen mode={mode} onSuggest={sendMessage} suggestions={suggestions} loading={suggestionsLoading} />
        ) : (
          messages.map(msg => (
            <MessageBubble key={msg.id} message={msg} />
          ))
        )}
        <div ref={bottomRef} />
      </div>

      {/* 输入区 */}
      <div className="flex-shrink-0 px-6 py-4 border-t border-pink-50">
        <div className={`bg-white border rounded-2xl px-4 pt-3 pb-2 shadow-sm
          transition-all duration-200
          ${loading
            ? 'border-xhs-red/30 shadow-xhs-red/10'
            : 'border-pink-100 hover:border-xhs-red/30'
          }`}
        >
          {/* 文本输入 */}
          <textarea
            ref={textareaRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              !userId
                ? '请先在右上角设置用户 ID…'
                : mode === 'analysis'
                  ? '问我任何关于你收藏的问题...'
                  : '搜索你的收藏内容...'
            }
            disabled={loading || !userId}
            rows={1}
            className="w-full resize-none outline-none text-sm text-gray-800 placeholder:text-gray-400
                       leading-relaxed bg-transparent disabled:opacity-50 max-h-[120px] mb-2"
          />

          {/* 底部工具栏：模式切换 + 发送 */}
          <div className="flex items-center justify-between">
            {/* 模式切换胶囊 */}
            <div className="flex items-center gap-1">
              <ModeChip
                active={mode === 'search'}
                icon={<Search size={12} />}
                label="搜索模式"
                onClick={() => setMode('search')}
                disabled={loading}
              />
              <ModeChip
                active={mode === 'analysis'}
                icon={<Sparkles size={12} />}
                label="分析模式"
                onClick={() => setMode('analysis')}
                disabled={loading}
              />
            </div>

            <div className="flex items-center gap-2">
              {/* 提示文字 */}
              <span className="text-[10px] text-gray-300 hidden sm:block">
                Enter 发送 · Shift+Enter 换行
              </span>

              {/* 停止 / 发送按钮 */}
              {loading ? (
                <button
                  onClick={stopStream}
                  className="w-8 h-8 rounded-xl bg-gray-100 hover:bg-red-50 hover:text-red-500
                             flex items-center justify-center text-gray-400
                             active:scale-95 transition-all duration-150"
                  title="停止生成"
                >
                  <Square size={13} />
                </button>
              ) : (
                <button
                  onClick={() => sendMessage()}
                  disabled={!input.trim() || !userId}
                  className="w-8 h-8 rounded-xl bg-gradient-to-br from-xhs-red to-xhs-pink
                             flex items-center justify-center shadow-sm
                             hover:shadow-md active:scale-95 transition-all duration-150
                             disabled:opacity-40 disabled:cursor-not-allowed disabled:shadow-none"
                >
                  <Send size={14} className="text-white" />
                </button>
              )}
            </div>
          </div>
        </div>

        {/* 底部状态文字 */}
        <p className="text-[11px] text-gray-400 mt-2 text-center">
          {noteCount != null
            ? `基于你的 ${noteCount} 篇收藏，随时为你解答`
            : '基于你的收藏，随时为你解答'
          }
        </p>
      </div>
    </div>
  )
}

/* ── 模式胶囊按钮 ────────────────────────────────────────────── */

function ModeChip({ active, icon, label, onClick, disabled }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium
                  transition-all duration-150 disabled:cursor-not-allowed
                  ${active
                    ? 'bg-gradient-to-r from-xhs-red to-xhs-pink text-white shadow-sm'
                    : 'border border-pink-200 text-gray-500 hover:border-xhs-red/40 hover:text-xhs-red'
                  }`}
    >
      {icon}
      {label}
    </button>
  )
}

/* ── 欢迎页（空会话时显示） ──────────────────────────────────── */

function WelcomeScreen({ mode, onSuggest, suggestions, loading }) {
  const DEFAULT_SUGGESTIONS = [
    '面试经验有哪些总结？',
    '有没有旅行攻略推荐？',
    '求职简历怎么写？',
    '好用的生产力工具？',
  ]
  const pending = loading || suggestions === null
  const items = pending ? [] : (suggestions.length > 0 ? suggestions : DEFAULT_SUGGESTIONS)

  return (
    <div className="flex flex-col items-center justify-center min-h-[60%] gap-8 animate-fade-in">
      <div className="text-center">
        <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-xhs-red to-xhs-pink
                        flex items-center justify-center mx-auto shadow-lg mb-4">
          <Sparkles size={28} className="text-white" />
        </div>
        <h2 className="text-xl font-bold text-gray-800">你好，我是 KnoNote</h2>
        <p className="text-sm text-gray-500 mt-2 max-w-sm leading-relaxed">
          {mode === 'analysis'
            ? '我会读取你的小红书收藏笔记，用 AI 总结回答你的问题'
            : '搜索你的小红书收藏，找到最相关的笔记'}
        </p>
      </div>

      <div className="w-full max-w-md">
        <div className="flex items-center gap-2 mb-3">
          <Lightbulb size={13} className="text-xhs-pink" />
          <span className="text-xs text-gray-400 font-medium">
            {pending ? '正在生成建议...' : '试试这些问题'}
          </span>
        </div>
        <div className="grid grid-cols-2 gap-2">
          {pending ? (
            Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-[52px] rounded-xl bg-gray-100 animate-pulse" />
            ))
          ) : (
            items.map(s => (
              <button
                key={s}
                onClick={() => onSuggest(s)}
                className="text-left text-sm px-4 py-3 rounded-xl bg-white border border-pink-100
                           hover:border-xhs-red/40 hover:shadow-card hover:text-xhs-red
                           transition-all duration-200 text-gray-600 leading-snug"
              >
                {s}
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
