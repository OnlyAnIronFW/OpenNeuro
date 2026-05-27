import { useState, useEffect, useCallback } from 'react'
import { Zap, RefreshCw, FileText, CheckCircle, XCircle } from 'lucide-react'

function renderText(text: string): string {
  let html = text
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong class="text-zinc-100">$1</strong>')
  html = html.replace(/`([^`]+)`/g, '<code class="bg-zinc-950 px-1 rounded text-emerald-400 text-xs">$1</code>')
  html = html.replace(/^- (.+)$/gm, '<li class="text-zinc-300 ml-4 list-disc">$1</li>')
  html = html.replace(/\n/g, '<br/>')
  return html
}

function SkillCard({ name, selected, onClick }: { name: string; selected: boolean; onClick: () => void }) {
  const displayName = name.replace('.json', '').replace('.md', '').replace('skill_', '')
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-2 px-3 py-2.5 rounded-lg text-xs border transition-colors ${
        selected
          ? 'bg-zinc-800 border-zinc-600 text-zinc-200'
          : 'bg-zinc-900 border-zinc-800 text-zinc-400 hover:bg-zinc-800/50'
      }`}
    >
      <Zap size={12} className={selected ? 'text-amber-400' : 'text-zinc-600'} />
      <span className="truncate">{displayName}</span>
      <span className="ml-auto flex items-center gap-0.5 text-emerald-600">
        <CheckCircle size={10} /> active
      </span>
    </button>
  )
}

export function SkillLibrary() {
  const [skills, setSkills] = useState<string[]>([])
  const [content, setContent] = useState('')
  const [selected, setSelected] = useState('')

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/skills/list')
      setSkills((await r.json()).skills || [])
    } catch {}
  }, [])

  useEffect(() => { load() }, [load])

  const open = async (name: string) => {
    setSelected(name)
    try {
      const r = await fetch(`/api/skills/file/${name}`)
      setContent((await r.json()).content || '')
    } catch {}
  }

  return (
    <div className="flex gap-4 max-h-[calc(100vh-3rem)]">
      <div className="w-56 bg-zinc-900 rounded-lg border border-zinc-800 flex flex-col shrink-0">
        <div className="flex items-center justify-between p-2 border-b border-zinc-800">
          <span className="text-xs font-medium text-zinc-300 flex items-center gap-1">
            <Zap size={14} /> 技能库
          </span>
          <button onClick={load} className="p-1 text-zinc-500 hover:text-zinc-300">
            <RefreshCw size={12} />
          </button>
        </div>
        <div className="flex-1 overflow-auto p-2 space-y-1">
          {skills.map(s => (
            <SkillCard key={s} name={s} selected={selected === s} onClick={() => open(s)} />
          ))}
          {skills.length === 0 && (
            <p className="text-xs text-zinc-600 p-3">暂无技能。直播后自动提炼。</p>
          )}
        </div>
      </div>
      <div className="flex-1 bg-zinc-900 rounded-lg border border-zinc-800 p-4 overflow-auto">
        {content ? (
          <div className="text-sm text-zinc-300 leading-relaxed" dangerouslySetInnerHTML={{ __html: renderText(content) }} />
        ) : (
          <div className="text-center text-zinc-600 mt-20 text-sm">选择技能查看详情</div>
        )}
      </div>
    </div>
  )
}
