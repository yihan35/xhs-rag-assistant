import { Search, BrainCircuit } from 'lucide-react'

export default function ModeToggle({ mode, onChange }) {
  return (
    <div className="flex items-center bg-pink-50 rounded-xl p-1 gap-1">
      <ModeBtn
        active={mode === 'search'}
        icon={<Search size={13} />}
        label="搜索"
        title="直接返回相关帖子，不调用 AI"
        onClick={() => onChange('search')}
      />
      <ModeBtn
        active={mode === 'analysis'}
        icon={<BrainCircuit size={13} />}
        label="分析"
        title="AI 读取相关帖子后生成总结回答"
        onClick={() => onChange('analysis')}
      />
    </div>
  )
}

function ModeBtn({ active, icon, label, title, onClick }) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
                  transition-all duration-200
                  ${active
                    ? 'bg-white text-xhs-red shadow-sm'
                    : 'text-gray-500 hover:text-gray-700'
                  }`}
    >
      {icon}
      {label}
    </button>
  )
}
