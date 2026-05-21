import { useState } from 'react'
import { X, Settings, Save } from 'lucide-react'

export default function SettingsModal({ userId, onSave, onClose }) {
  const [draft, setDraft] = useState(userId)

  const handleSave = () => {
    if (draft.trim()) {
      onSave(draft.trim())
      onClose()
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/30 backdrop-blur-sm z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl shadow-chat w-full max-w-sm p-6 animate-slide-up"
        onClick={e => e.stopPropagation()}
      >
        {/* 标题 */}
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-xl bg-pink-50 flex items-center justify-center">
              <Settings size={16} className="text-xhs-red" />
            </div>
            <h2 className="text-base font-semibold text-gray-800">设置</h2>
          </div>
          <button
            onClick={onClose}
            className="w-7 h-7 rounded-lg hover:bg-gray-100 flex items-center justify-center transition-colors"
          >
            <X size={16} className="text-gray-500" />
          </button>
        </div>

        {/* 表单 */}
        <div className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1.5">
              小红书用户 ID
            </label>
            <input
              type="text"
              value={draft}
              onChange={e => setDraft(e.target.value)}
              placeholder="640c4bcc000000002a0088a8"
              className="input-base font-mono text-xs"
              onKeyDown={e => { if (e.key === 'Enter') handleSave() }}
            />
            <p className="text-[11px] text-gray-400 mt-1.5">
              在 ingest.py 中查看 MY_USER_ID 的值
            </p>
          </div>
        </div>

        {/* 按钮 */}
        <div className="flex gap-2 mt-6">
          <button
            onClick={onClose}
            className="flex-1 py-2.5 rounded-xl text-sm text-gray-600 bg-gray-100 hover:bg-gray-200 transition-colors"
          >
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={!draft.trim()}
            className="flex-1 btn-primary flex items-center justify-center gap-1.5"
          >
            <Save size={14} />
            保存
          </button>
        </div>
      </div>
    </div>
  )
}
