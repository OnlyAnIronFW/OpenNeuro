import { MessageCircle, User, Settings, Activity, ScrollText, Brain,
         Play, Zap, Book, Beaker, FileText, Wrench } from 'lucide-react'

type Page = 'live' | 'persona' | 'memory' | 'skills' | 'knowledge' |
            'replay' | 'iteration' | 'test' | 'config' | 'monitor' | 'logs' | 'settings'

const items: { id: Page; label: string; icon: typeof MessageCircle }[] = [
  { id: 'live', label: '直播控制', icon: MessageCircle },
  { id: 'persona', label: '人设编辑', icon: User },
  { id: 'memory', label: '观众记忆', icon: Brain },
  { id: 'skills', label: '技能库', icon: Zap },
  { id: 'knowledge', label: '知识库', icon: Book },
  { id: 'replay', label: '录制回放', icon: Play },
  { id: 'iteration', label: '自迭代', icon: Beaker },
  { id: 'test', label: '测试中心', icon: FileText },
  { id: 'config', label: '配置中心', icon: Settings },
  { id: 'monitor', label: '监控', icon: Activity },
  { id: 'logs', label: '日志', icon: ScrollText },
  { id: 'settings', label: '系统设置', icon: Wrench },
]

export function Sidebar({ page, onNavigate }: { page: Page; onNavigate: (p: Page) => void }) {
  return (
    <aside className="w-44 bg-zinc-900 border-r border-zinc-800 flex flex-col shrink-0">
      <div className="p-3 border-b border-zinc-800">
        <h1 className="text-sm font-bold text-zinc-100">AI Streamer</h1>
        <p className="text-[10px] text-zinc-500">管理控制台</p>
      </div>
      <nav className="flex-1 p-1.5 space-y-0.5 overflow-auto">
        {items.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => onNavigate(id)}
            className={`w-full flex items-center gap-2 px-2.5 py-1.5 rounded text-xs transition-colors ${
              page === id
                ? 'bg-zinc-800 text-zinc-100'
                : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50'
            }`}
          >
            <Icon size={13} />
            {label}
          </button>
        ))}
      </nav>
      <div className="p-2 border-t border-zinc-800 text-[10px] text-zinc-600">
        v2.0
      </div>
    </aside>
  )
}
