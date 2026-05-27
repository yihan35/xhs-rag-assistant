import { useState } from 'react'
import { Plus, Trash2, Sparkles, BookMarked, ChevronLeft, RefreshCw, FileText, Film } from 'lucide-react'

export default function Sidebar({
  sessions,
  currentId,
  onNewSession,
  onSelectSession,
  onDeleteSession,
  notes,
  notesLoading,
  stats,
  updateCount = 0,
  onRefreshNotes,
  onSync,
  onMarkNoteSeen,
  syncState  = 'idle',
  syncError  = '',
}) {
  const [showNotes, setShowNotes] = useState(false)

  return (
    <aside className="flex flex-col h-full bg-white border-r border-pink-100 relative overflow-hidden">
      {/* 品牌标题 */}
      <div className="px-4 pt-5 pb-4 border-b border-pink-50 flex-shrink-0">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-xhs-red to-xhs-pink flex items-center justify-center shadow-sm">
            <Sparkles size={16} className="text-white" />
          </div>
          <div>
            <h1 className="text-base font-bold text-gray-900 leading-none">KnoNote</h1>
            <p className="text-[10px] text-gray-400 mt-0.5">小红书收藏智能助手</p>
          </div>
        </div>

        {/* 同步状态 */}
        <div className="mt-3">
          <SyncStatus
            stats={stats}
            updateCount={updateCount}
            onRefresh={onRefreshNotes}
            onSync={onSync}
            syncState={syncState}
            syncError={syncError}
          />
        </div>
      </div>

      {/* 主内容区：会话列表 or 收藏列表 */}
      <div className="flex-1 overflow-hidden flex flex-col">
        {showNotes ? (
          <NotesView
            notes={notes}
            loading={notesLoading}
            onBack={() => setShowNotes(false)}
            onRefresh={onRefreshNotes}
            onMarkNoteSeen={onMarkNoteSeen}
          />
        ) : (
          <SessionsView
            sessions={sessions}
            currentId={currentId}
            onNew={onNewSession}
            onSelect={onSelectSession}
            onDelete={onDeleteSession}
          />
        )}
      </div>

      {/* 底部：「我的收藏」入口（仅在会话列表视图显示） */}
      {!showNotes && (
        <div className="flex-shrink-0 px-3 py-3 border-t border-pink-50">
          <button
            onClick={() => setShowNotes(true)}
            className="w-full flex items-center gap-2 px-3 py-2.5 rounded-xl text-sm text-gray-600
                       hover:bg-pink-50 hover:text-xhs-red transition-all duration-200"
          >
            <BookMarked size={15} className="text-xhs-pink" />
            <span>我的收藏</span>
            <div className="ml-auto flex items-center gap-1.5">
              {updateCount > 0 && (
                <span className="flex items-center justify-center min-w-[18px] h-[18px] px-1
                                 rounded-full bg-amber-400 text-white text-[10px] font-bold">
                  {updateCount}
                </span>
              )}
              {stats?.sqlite_total > 0 && (
                <span className="text-xs text-gray-400">{stats.sqlite_total} 篇</span>
              )}
            </div>
          </button>
        </div>
      )}
    </aside>
  )
}

/* ── 同步状态徽章 ─────────────────────────────────────────────── */

function SyncButton({ syncState, onSync }) {
  const isRunning = syncState === 'running'
  const isDone    = syncState === 'done'
  const isError   = syncState === 'error'

  let label, iconClass
  if (isRunning) { label = '同步中...'; iconClass = 'animate-spin' }
  else if (isDone)  { label = '✓ 完成' }
  else if (isError) { label = '重试' }
  else              { label = '同步' }

  return (
    <button
      onClick={onSync}
      disabled={isRunning}
      title="同步小红书收藏夹"
      className={`
        flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium border
        transition-all duration-150 disabled:cursor-not-allowed
        ${isDone
          ? 'border-green-300 text-green-600 bg-green-50'
          : isError
            ? 'border-red-300 text-red-500 bg-red-50'
            : isRunning
              ? 'border-xhs-red/30 text-xhs-red/60 bg-xhs-rose'
              : 'border-xhs-red/40 text-xhs-red bg-transparent hover:bg-xhs-rose hover:border-xhs-red/60'
        }
      `}
    >
      <RefreshCw size={10} className={iconClass} />
      {label}
    </button>
  )
}

function SyncStatus({ stats, updateCount = 0, onRefresh, onSync, syncState, syncError }) {
  const isRunning = syncState === 'running'

  // 同步中：整行替换为进度提示
  if (isRunning) {
    return (
      <div className="flex items-center gap-1.5 text-xs text-xhs-red">
        <RefreshCw size={11} className="animate-spin" />
        <span>正在同步收藏夹…</span>
      </div>
    )
  }

  // 未加载过 stats（首次进入）
  if (!stats) {
    return (
      <div className="flex items-center justify-between">
        <span className="text-xs text-gray-400">尚未同步</span>
        <SyncButton syncState={syncState} onSync={onSync} />
      </div>
    )
  }

  const n            = stats.sqlite_total ?? 0
  return (
    <div className="flex flex-col gap-1">
      {/* 主状态行 */}
      <div className="flex items-center justify-between gap-2">
        {n === 0 ? (
          <span className="flex items-center gap-1.5 text-xs text-amber-500">
            <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
            收藏夹为空
          </span>
        ) : (
          <span className="flex items-center gap-1.5 text-xs text-gray-500">
            <span className="w-1.5 h-1.5 rounded-full bg-green-400" />
            知识库已就绪 · <span className="font-medium text-gray-700">{n}</span> 篇
          </span>
        )}
        <SyncButton syncState={syncState} onSync={onSync} />
      </div>

      {/* 更新提醒行 */}
      {updateCount > 0 && (
        <div className="flex items-center gap-1.5 text-xs text-amber-500">
          <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
          <span>{updateCount} 篇内容有更新</span>
        </div>
      )}

      {/* 同步失败提示 */}
      {syncState === 'error' && syncError && (
        <p className="text-[10px] text-red-400 leading-snug mt-0.5 line-clamp-2" title={syncError}>
          同步失败：{syncError}
        </p>
      )}
    </div>
  )
}

/* ── 会话列表视图 ─────────────────────────────────────────────── */

function SessionsView({ sessions, currentId, onNew, onSelect, onDelete }) {
  return (
    <>
      {/* 新建会话按钮 */}
      <div className="px-3 pt-3 pb-2 flex-shrink-0">
        <button
          onClick={onNew}
          className="w-full flex items-center justify-center gap-1.5 py-2.5 rounded-xl
                     border border-xhs-red/30 text-xhs-red text-sm font-medium
                     hover:bg-xhs-rose hover:border-xhs-red/50
                     active:scale-[0.98] transition-all duration-150"
        >
          <Plus size={15} />
          新建会话
        </button>
      </div>

      {/* 历史列表 */}
      <div className="flex-1 overflow-y-auto px-2 py-1 space-y-0.5">
        {sessions.length === 0 ? (
          <p className="text-xs text-gray-400 text-center py-8">暂无会话记录</p>
        ) : (
          sessions.map(session => (
            <SessionItem
              key={session.id}
              session={session}
              active={session.id === currentId}
              onSelect={() => onSelect(session.id)}
              onDelete={() => onDelete(session.id)}
            />
          ))
        )}
      </div>
    </>
  )
}

function SessionItem({ session, active, onSelect, onDelete }) {
  const title = session.title || '新会话'
  const relTime = getRelativeTime(session.createdAt)

  return (
    <div
      className={`group relative flex items-center gap-2 px-3 py-2.5 rounded-xl cursor-pointer
                  transition-all duration-150
                  ${active
                    ? 'bg-xhs-rose border border-xhs-red/25'
                    : 'hover:bg-pink-50 border border-transparent'
                  }`}
      onClick={onSelect}
    >
      <div className="flex-1 min-w-0">
        <p className={`text-sm truncate leading-snug
                       ${active ? 'text-xhs-red font-medium' : 'text-gray-700'}`}>
          {title}
        </p>
        <p className="text-[10px] text-gray-400 mt-0.5">{relTime}</p>
      </div>

      {/* 删除按钮：hover 才显示 */}
      <button
        onClick={e => { e.stopPropagation(); onDelete() }}
        className="flex-shrink-0 opacity-0 group-hover:opacity-100 w-5 h-5
                   flex items-center justify-center rounded text-gray-400
                   hover:text-red-500 hover:bg-red-50 transition-all duration-150"
        title="删除会话"
      >
        <Trash2 size={12} />
      </button>
    </div>
  )
}

/* ── 收藏列表视图 ─────────────────────────────────────────────── */

function NotesView({ notes, loading, onBack, onRefresh, onMarkNoteSeen }) {
  return (
    <>
      {/* 顶栏 */}
      <div className="flex items-center gap-2 px-3 pt-3 pb-2 flex-shrink-0">
        <button
          onClick={onBack}
          className="w-7 h-7 rounded-lg hover:bg-pink-50 flex items-center justify-center
                     text-gray-400 hover:text-xhs-red transition-colors"
        >
          <ChevronLeft size={16} />
        </button>
        <span className="text-sm font-medium text-gray-700">我的收藏</span>
        <button
          onClick={onRefresh}
          disabled={loading}
          className="ml-auto text-gray-400 hover:text-xhs-red transition-colors disabled:opacity-40"
          title="刷新"
        >
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {/* 笔记列表 */}
      <div className="flex-1 overflow-y-auto px-2 py-1 space-y-0.5">
        {loading ? (
          <div className="flex flex-col items-center justify-center py-12 gap-3">
            <div className="w-7 h-7 border-2 border-xhs-red/30 border-t-xhs-red rounded-full animate-spin" />
            <p className="text-xs text-gray-400">加载中…</p>
          </div>
        ) : notes.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 gap-2">
            <BookMarked size={24} className="text-pink-200" />
            <p className="text-xs text-gray-400 text-center px-4">收藏夹为空<br />请先运行 sync_xhs.sh</p>
          </div>
        ) : (
          notes.map(note => (
            <CompactNoteItem key={note.note_id} note={note} onMarkSeen={onMarkNoteSeen} />
          ))
        )}
      </div>
    </>
  )
}

function CompactNoteItem({ note, onMarkSeen }) {
  const hasCover = note.cover_url?.startsWith('http')
  const isVideo  = note.note_type === 'video'

  return (
    <a
      href={note.note_url || '#'}
      target="_blank"
      rel="noopener noreferrer"
      className="flex gap-2.5 p-2.5 rounded-xl hover:bg-pink-50 transition-colors group"
      onClick={e => {
        if (!note.note_url) e.preventDefault()
        if (note.has_unread_update) onMarkSeen?.(note.note_id)
      }}
    >
      <div className="flex-shrink-0 w-10 h-10 rounded-lg overflow-hidden bg-pink-100">
        {hasCover ? (
          <img src={note.cover_url} alt={note.title}
               className="w-full h-full object-cover" onError={e => { e.target.style.display = 'none' }} />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            {isVideo ? <Film size={14} className="text-xhs-pink" /> : <FileText size={14} className="text-xhs-pink" />}
          </div>
        )}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-xs font-medium text-gray-700 line-clamp-2 leading-snug group-hover:text-xhs-red transition-colors">
          {note.title || '无标题'}
        </p>
        <div className="flex items-center gap-1 mt-1">
          {note.has_unread_update && (
            <span
              className="inline-block w-1.5 h-1.5 rounded-full bg-amber-400"
              title={`内容已更新：${new Date(note.content_changed_at).toLocaleDateString('zh-CN')}`}
            />
          )}
          {note.indexed === 1 && !note.has_unread_update && (
            <span className="inline-block w-1.5 h-1.5 rounded-full bg-green-400" title="已向量化" />
          )}
          {note.has_unread_update && (
            <span className="text-[10px] font-medium text-amber-500">已更新</span>
          )}
        </div>
      </div>
    </a>
  )
}

/* ── 工具函数 ─────────────────────────────────────────────────── */

function getRelativeTime(isoStr) {
  if (!isoStr) return ''
  const diff = Date.now() - new Date(isoStr).getTime()
  const days  = Math.floor(diff / 86400000)
  const hours = Math.floor(diff / 3600000)
  const mins  = Math.floor(diff / 60000)

  if (days === 0) {
    if (hours === 0) return mins <= 1 ? '刚刚' : `${mins} 分钟前`
    return `${hours} 小时前`
  }
  if (days === 1) return '昨天'
  if (days < 7)   return `${days} 天前`
  return new Date(isoStr).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}
