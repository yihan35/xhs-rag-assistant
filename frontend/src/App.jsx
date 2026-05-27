import { useState, useEffect, useCallback } from 'react'
import { Settings, PanelLeftClose, PanelLeft } from 'lucide-react'
import Sidebar from './components/Sidebar.jsx'
import ChatArea from './components/ChatArea.jsx'
import SettingsModal from './components/SettingsModal.jsx'
import { useNotes, useSync, useClassify } from './hooks/useApi.js'
import { useSessions } from './hooks/useSessions.js'

const DEFAULT_USER_ID = '640c4bcc000000002a0088a8'
const LS_KEY = 'xhs_user_id'

export default function App() {
  const [userId, setUserId]         = useState(() => localStorage.getItem(LS_KEY) || DEFAULT_USER_ID)
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [showSettings, setShowSettings] = useState(false)

  const { notes, loading: notesLoading, stats, fetchNotes, category, setCategory } = useNotes(userId)
  const { startSync, syncState, syncError } = useSync(() => fetchNotes(userId))
  const { startClassify, classifyState } = useClassify(() => fetchNotes(userId))
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
    if (userId) fetchNotes(userId, category)
  }, [userId, category])

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
          notes={notes}
          notesLoading={notesLoading}
          stats={stats}
          onRefreshNotes={(cat) => { setCategory(cat || ''); fetchNotes(userId, cat || '') }}
          onSync={() => startSync(userId)}
          syncState={syncState}
          syncError={syncError}
          userId={userId}
          onClassify={() => startClassify(userId)}
          classifyState={classifyState}
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
    </div>
  )
}
