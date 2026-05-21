import { Heart, FileText, Film } from 'lucide-react'

export default function NoteCard({ note, compact = false, onClick, active = false }) {
  const hasCover = note.cover_url && note.cover_url.startsWith('http')
  const isVideo  = note.note_type === 'video'

  if (compact) {
    // 侧边栏紧凑版
    return (
      <button
        onClick={onClick}
        className={`w-full text-left flex gap-3 p-3 rounded-xl transition-all duration-200 group
          ${active
            ? 'bg-xhs-rose border border-xhs-red/30 shadow-sm'
            : 'hover:bg-pink-50 border border-transparent'
          }`}
      >
        {/* 封面缩略图 */}
        <div className="flex-shrink-0 w-12 h-12 rounded-lg overflow-hidden bg-pink-100">
          {hasCover ? (
            <img
              src={note.cover_url}
              alt={note.title}
              className="w-full h-full object-cover"
              onError={e => { e.target.style.display = 'none' }}
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center">
              {isVideo
                ? <Film size={16} className="text-xhs-pink" />
                : <FileText size={16} className="text-xhs-pink" />
              }
            </div>
          )}
        </div>

        {/* 内容 */}
        <div className="flex-1 min-w-0">
          <p className={`text-sm font-medium line-clamp-2 leading-snug
            ${active ? 'text-xhs-red' : 'text-gray-800 group-hover:text-xhs-red'}`}>
            {note.title || '无标题'}
          </p>
          <div className="flex items-center gap-2 mt-1">
            {note.indexed === 1 && (
              <span className="w-1.5 h-1.5 rounded-full bg-green-400 flex-shrink-0" title="已向量化" />
            )}
            {note.likes > 0 && (
              <span className="flex items-center gap-0.5 text-xs text-gray-400">
                <Heart size={10} />
                {note.likes > 999 ? `${(note.likes / 1000).toFixed(1)}k` : note.likes}
              </span>
            )}
          </div>
        </div>
      </button>
    )
  }

  // 搜索结果来源卡片（稍大）
  return (
    <a
      href={note.note_url || '#'}
      target="_blank"
      rel="noopener noreferrer"
      className="card hover:shadow-card-hover group flex gap-3 p-4 cursor-pointer"
      onClick={e => { if (!note.note_url) e.preventDefault() }}
    >
      <div className="flex-shrink-0 w-16 h-16 rounded-xl overflow-hidden bg-pink-100">
        {hasCover ? (
          <img
            src={note.cover_url}
            alt={note.title}
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
            onError={e => { e.target.style.display = 'none' }}
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            {isVideo
              ? <Film size={20} className="text-xhs-pink" />
              : <FileText size={20} className="text-xhs-pink" />
            }
          </div>
        )}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-gray-800 group-hover:text-xhs-red line-clamp-2 leading-snug transition-colors">
          {note.title || '无标题'}
        </p>
        {note.tags?.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-2">
            {note.tags.slice(0, 3).map(tag => (
              <span key={tag} className="tag">{tag}</span>
            ))}
          </div>
        )}
        {note.distance !== undefined && (
          <div className="mt-1.5">
            <span className="text-xs text-gray-400">
              相关度 {Math.round((1 - note.distance) * 100)}%
            </span>
          </div>
        )}
      </div>
    </a>
  )
}
