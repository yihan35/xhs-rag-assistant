import { useState, useEffect, useCallback, useMemo } from 'react'
import { Settings, PanelLeftClose, PanelLeft, Bell, X } from 'lucide-react'
import Sidebar from './components/Sidebar.jsx'
import ChatArea from './components/ChatArea.jsx'
import SettingsModal from './components/SettingsModal.jsx'
import { useFavoriteUpdates, useNotes, useSync } from './hooks/useApi.js'
import { useSessions } from './hooks/useSessions.js'

const DEFAULT_USER_ID = '640c4bcc000000002a0088a8'
const LS_KEY = 'xhs_user_id'

export default function App() {
  const [userId, setUserId]         = useState(() => localStorage.getItem(LS_KEY) || DEFAULT_USER_ID)
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [showSettings, setShowSettings] = useState(false)
  const [updateToast, setUpdateToast] = useState(null)

  const { notes, loading: notesLoading, stats, fetchNotes } = useNotes(userId)
  const handleNewFavoriteUpdates = useCallback(items => {
    const first = items[0]
    setUpdateToast({
      total: items.length,
      title: first?.title || '无标题',
      noteId: first?.note_id,
    })
  }, [])
  const {
    updateCount,
    updatedNoteIds,
    fetchUpdates,
    markSeen,
  } = useFavoriteUpdates(userId, {
    onNewUpdates: handleNewFavoriteUpdates,
  })
  const { startSync, syncState, syncError } = useSync(() => {
    fetchNotes(userId)
    fetchUpdates(userId)
  })
  const {
    sessions,
    currentId,
    currentSession,
    createSession,
    deleteSession,
    selectSession,
    updateSession,
  } = useSessions()

  useEffect(() => {
    if (userId) fetchNotes(userId)
  }, [userId])

  useEffect(() => {
    if (!updateToast) return undefined
    const timer = setTimeout(() => setUpdateToast(null), 6000)
    return () => clearTimeout(timer)
  }, [updateToast])

  const notesWithUpdateState = useMemo(() => (
    notes.map(note => ({
      ...note,
      has_unread_update: updatedNoteIds.has(note.note_id),
    }))
  ), [notes, updatedNoteIds])

  const handleSaveUserId = useCallback(uid => {
    localStorage.setItem(LS_KEY, uid)
    setUserId(uid)
  }, [])

  const handleUpdateCurrentSession = useCallback((updater) => {
    if (currentId) updateSession(currentId, updater)
  }, [currentId, updateSession])

  return (
    <div className="flex h-screen overflow-hidden bg-xhs-light">
      {/* 侧边栏 */}
      <div
        className={`flex-shrink-0 transition-all duration-300 ease-in-out overflow-hidden
          ${sidebarOpen ? 'w-64' : 'w-0'}`}
      >
        <Sidebar
          sessions={sessions}
          currentId={currentId}
          onNewSession={createSession}
          onSelectSession={selectSession}
          onDeleteSession={deleteSession}
          notes={notesWithUpdateState}
          notesLoading={notesLoading}
          stats={stats}
          updateCount={updateCount}
          onRefreshNotes={() => fetchNotes(userId)}
          onSync={() => startSync(userId)}
          onMarkNoteSeen={noteId => markSeen(noteId)}
          syncState={syncState}
          syncError={syncError}
        />
      </div>

      {/* 主区域 */}
      <main className="flex-1 flex flex-col min-w-0 bg-xhs-light">
        {/* 顶部工具栏 */}
        <div className="flex items-center justify-between px-4 py-3 flex-shrink-0">
          <button
            onClick={() => setSidebarOpen(v => !v)}
            className="w-8 h-8 rounded-xl hover:bg-white hover:shadow-sm flex items-center justify-center
                       text-gray-400 hover:text-xhs-red transition-all duration-200"
            title={sidebarOpen ? '收起侧边栏' : '展开侧边栏'}
          >
            {sidebarOpen ? <PanelLeftClose size={17} /> : <PanelLeft size={17} />}
          </button>
          <button
            onClick={() => setShowSettings(true)}
            className="w-8 h-8 rounded-xl hover:bg-white hover:shadow-sm flex items-center justify-center
                       text-gray-400 hover:text-xhs-red transition-all duration-200"
            title="设置用户 ID"
          >
            <Settings size={16} />
          </button>
        </div>

        {/* 聊天区 */}
        <div className="flex-1 mx-4 mb-4 min-h-0 rounded-2xl bg-white shadow-card overflow-hidden">
          <ChatArea
            key={currentId}
            session={currentSession}
            onUpdateSession={handleUpdateCurrentSession}
            userId={userId}
            noteCount={stats?.sqlite_total ?? null}
          />
        </div>
      </main>

      {showSettings && (
        <SettingsModal
          userId={userId}
          onSave={handleSaveUserId}
          onClose={() => setShowSettings(false)}
        />
      )}
      {updateToast && (
        <div className="fixed right-4 top-4 z-50 w-[min(340px,calc(100vw-32px))] rounded-xl border border-amber-200 bg-white shadow-card p-3">
          <div className="flex items-start gap-3">
            <div className="w-8 h-8 rounded-full bg-amber-100 text-amber-500 flex items-center justify-center flex-shrink-0">
              <Bell size={16} />
            </div>
            <button
              onClick={() => updateToast.noteId && markSeen(updateToast.noteId)}
              className="flex-1 min-w-0 text-left"
            >
              <p className="text-sm font-semibold text-gray-800">收藏帖子已更新</p>
              <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">
                {updateToast.total > 1 ? `${updateToast.total} 篇帖子有新变化，包含「${updateToast.title}」` : `「${updateToast.title}」有新变化`}
              </p>
            </button>
            <button
              onClick={() => setUpdateToast(null)}
              className="w-6 h-6 rounded-md flex items-center justify-center text-gray-400 hover:text-gray-700 hover:bg-gray-100"
              title="关闭"
            >
              <X size={14} />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
