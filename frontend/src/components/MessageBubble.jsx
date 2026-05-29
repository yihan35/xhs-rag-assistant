import { useState } from 'react'
import { User, Sparkles, Loader2, FileText } from 'lucide-react'

export default function MessageBubble({ message }) {
  const { role, content, sources, mode, status } = message

  /* ── 用户气泡 ─────────────────────────────────────────────── */
  if (role === 'user') {
    return (
      <div className="flex justify-end animate-fade-in">
        <div className="flex items-end gap-2 max-w-[80%]">
          <div className="bg-gradient-to-br from-xhs-red to-xhs-pink text-white
                          rounded-2xl rounded-br-sm px-4 py-3 shadow-sm">
            <p className="text-sm leading-relaxed whitespace-pre-wrap">{content}</p>
          </div>
          <div className="flex-shrink-0 w-7 h-7 rounded-full bg-gray-200
                          flex items-center justify-center mb-0.5">
            <User size={14} className="text-gray-500" />
          </div>
        </div>
      </div>
    )
  }

  /* ── AI 气泡（Perplexity 布局） ───────────────────────────── */
  const showSources  = sources?.length > 0
  const showSpinner  = status === 'searching' || status === 'found'
  const isStreaming  = status === 'streaming'
  const isDone       = status === 'done'
  const isEmpty      = status === 'empty'
  const isError      = status === 'error'

  const sourcesVisible = showSources && status !== 'searching'

  return (
    <div className="flex justify-start animate-slide-up">
      <div className="flex items-start gap-2 max-w-[92%] w-full">
        {/* Logo */}
        <div className="flex-shrink-0 w-7 h-7 rounded-full bg-gradient-to-br from-xhs-red to-xhs-pink
                        flex items-center justify-center mt-0.5 shadow-sm">
          <Sparkles size={13} className="text-white" />
        </div>

        <div className="flex-1 min-w-0 space-y-3">

          {/* ① 来源卡片区（来源到来后淡入） */}
          {showSources && (
            <div className={sourcesVisible ? 'animate-source-in' : 'opacity-0'}>
              <p className="text-xs text-gray-400 mb-2">
                找到 <span className="font-medium text-gray-600">{sources.length}</span> 篇相关笔记
              </p>
              <div className="flex gap-2 overflow-x-auto pb-1"
                   style={{ scrollbarWidth: 'thin' }}>
                {sources.map((src, i) => (
                  <SourceCard key={src.note_id || i} source={src} />
                ))}
              </div>
            </div>
          )}

          {/* ② AI 回答区 */}
          <div className="bg-white rounded-2xl rounded-tl-sm shadow-card px-4 py-3">

            {/* 状态指示器 */}
            {showSpinner && (
              <div className="flex items-center gap-2 text-sm text-gray-400">
                <Loader2 size={14} className="animate-spin-status text-xhs-pink flex-shrink-0" />
                <span className="animate-pulse-soft">
                  {status === 'searching' ? '正在检索相关收藏...' : '大模型正在分析中...'}
                </span>
              </div>
            )}

            {/* 空结果 */}
            {isEmpty && (
              <p className="text-sm text-gray-400">
                没有找到相关收藏内容，试试换个关键词？
              </p>
            )}

            {/* 错误 */}
            {isError && (
              <p className="text-sm text-red-400">{content}</p>
            )}

            {/* search 模式完成：引导文字 */}
            {mode === 'search' && isDone && showSources && (
              <p className="text-sm text-gray-400">
                点击卡片查看原帖，或切换分析模式获取总结
              </p>
            )}

            {/* search 模式完成：无内容 */}
            {mode === 'search' && isDone && !showSources && (
              <p className="text-sm text-gray-400">
                没有找到相关收藏内容，试试换个关键词？
              </p>
            )}

            {/* analysis 模式：Markdown 回答 + 流式光标 */}
            {mode === 'analysis' && (isStreaming || isDone) && content && (
              <div className="text-sm text-gray-700 leading-relaxed">
                <AnswerContent text={content} showCursor={isStreaming} />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

/* ── 来源卡片 ────────────────────────────────────────────────── */

function SourceCard({ source }) {
  const [coverFailed, setCoverFailed] = useState(false)
  const hasCover = Boolean(source.cover_url) && !coverFailed

  return (
    <a
      href={source.note_url || '#'}
      target="_blank"
      rel="noopener noreferrer"
      onClick={e => { if (!source.note_url) e.preventDefault() }}
      className="flex-shrink-0 w-[200px] h-[72px] bg-white border border-pink-100 rounded-xl
                 flex gap-2.5 p-2 hover:border-xhs-red/40 hover:shadow-card
                 transition-all duration-200 cursor-pointer group"
    >
      {/* 封面 56×56 */}
      <div className="flex-shrink-0 w-14 h-14 rounded-lg overflow-hidden bg-pink-50">
        {hasCover ? (
          <img
            src={source.cover_url}
            alt={source.title}
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
            onError={() => setCoverFailed(true)}
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <FileText size={16} className="text-pink-300" />
          </div>
        )}
      </div>

      {/* 右侧文字 */}
      <div className="flex-1 min-w-0 flex flex-col justify-between py-0.5">
        <p className="text-xs font-medium text-gray-800 line-clamp-2 leading-snug
                      group-hover:text-xhs-red transition-colors">
          {source.title || '无标题'}
        </p>
        {/* 小红书标识 */}
        <div className="flex items-center gap-1">
          <div className="w-3.5 h-3.5 rounded-sm bg-xhs-red flex items-center justify-center flex-shrink-0">
            <span className="text-white font-bold leading-none" style={{ fontSize: '7px' }}>书</span>
          </div>
          <span className="text-[10px] text-gray-400">小红书</span>
        </div>
      </div>
    </a>
  )
}

/* ── Markdown 渲染 ───────────────────────────────────────────── */

function AnswerContent({ text, showCursor }) {
  const lines = text.split('\n')

  return (
    <div className="space-y-1.5">
      {lines.map((line, i) => {
        const isLast = i === lines.length - 1

        if (!line.trim()) return <div key={i} className="h-2" />

        // H2 标题
        if (line.startsWith('## ')) {
          return (
            <p key={i} className="font-bold text-gray-800 mt-3 text-base">
              {line.slice(3)}
              {isLast && showCursor && <Cursor />}
            </p>
          )
        }
        // H3 标题
        if (line.startsWith('### ')) {
          return (
            <p key={i} className="font-semibold text-gray-800 mt-2">
              {line.slice(4)}
              {isLast && showCursor && <Cursor />}
            </p>
          )
        }
        // 无序列表
        if (/^[\s]*[-*•]\s/.test(line)) {
          const content = line.replace(/^[\s]*[-*•]\s/, '')
          return (
            <div key={i} className="flex gap-2">
              <span className="text-xhs-red mt-0.5 flex-shrink-0 leading-relaxed">•</span>
              <span>
                {renderInline(content)}
                {isLast && showCursor && <Cursor />}
              </span>
            </div>
          )
        }
        // 有序列表
        if (/^\s*\d+[.)、]\s/.test(line)) {
          const match = line.match(/^(\s*\d+[.)、]\s)(.*)/)
          if (match) {
            return (
              <div key={i} className="flex gap-2">
                <span className="text-xhs-red font-medium flex-shrink-0 leading-relaxed">
                  {match[1].trim()}
                </span>
                <span>
                  {renderInline(match[2])}
                  {isLast && showCursor && <Cursor />}
                </span>
              </div>
            )
          }
        }
        // 普通段落
        return (
          <p key={i}>
            {renderInline(line)}
            {isLast && showCursor && <Cursor />}
          </p>
        )
      })}
    </div>
  )
}

// 行内格式：**bold** / `code`
function renderInline(text) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/)
  return parts.map((p, i) => {
    if (p.startsWith('**') && p.endsWith('**'))
      return <strong key={i} className="font-semibold text-gray-900">{p.slice(2, -2)}</strong>
    if (p.startsWith('`') && p.endsWith('`'))
      return <code key={i} className="bg-pink-50 text-xhs-red px-1 py-0.5 rounded text-[0.8em] font-mono">{p.slice(1, -1)}</code>
    return p
  })
}

// 流式光标
function Cursor() {
  return (
    <span className="inline-block w-px h-[1em] bg-xhs-red ml-0.5 align-middle animate-cursor-blink" />
  )
}
