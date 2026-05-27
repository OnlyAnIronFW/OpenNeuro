import { useState, useEffect, useCallback } from 'react'
import { Book, FileText, RefreshCw, Edit3 } from 'lucide-react'

function renderMarkdown(text: string): string {
  let html = text
  html = html.replace(/^### (.+)$/gm, '<h3 class="text-base font-semibold text-zinc-200 mt-3 mb-1">$1</h3>')
  html = html.replace(/^## (.+)$/gm, '<h2 class="text-lg font-semibold text-zinc-200 mt-4 mb-2">$1</h2>')
  html = html.replace(/^# (.+)$/gm, '<h1 class="text-xl font-bold text-zinc-200 mt-4 mb-2">$1</h1>')
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong class="text-zinc-100">$1</strong>')
  html = html.replace(/\*(.+?)\*/g, '<em class="text-zinc-300">$1</em>')
  html = html.replace(/`([^`]+)`/g, '<code class="bg-zinc-950 px-1 rounded text-emerald-400 text-xs">$1</code>')
  html = html.replace(/^- (.+)$/gm, '<li class="text-zinc-300 ml-4 list-disc">$1</li>')
  html = html.replace(/\n/g, '<br/>')
  return html
}

export function KnowledgeBase() {
  const [files, setFiles] = useState<string[]>([])
  const [content, setContent] = useState('')
  const [selected, setSelected] = useState('')

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/knowledge/files')
      setFiles((await r.json()).files || [])
    } catch {}
  }, [])

  useEffect(() => { load() }, [load])

  const open = async (f: string) => {
    setSelected(f)
    try {
      const r = await fetch(`/api/knowledge/file/${f}`)
      setContent((await r.json()).content || '')
    } catch {}
  }

  return (
    <div className="flex gap-4 max-h-[calc(100vh-3rem)]">
      <div className="w-56 bg-zinc-900 rounded-lg border border-zinc-800 flex flex-col shrink-0">
        <div className="flex items-center justify-between p-2 border-b border-zinc-800">
          <span className="text-xs font-medium text-zinc-300 flex items-center gap-1">
            <Book size={14} /> 知识库
          </span>
          <button onClick={load} className="p-1 text-zinc-500 hover:text-zinc-300">
            <RefreshCw size={12} />
          </button>
        </div>
        <div className="flex-1 overflow-auto">
          {files.map(f => (
            <button
              key={f}
              onClick={() => open(f)}
              className={`w-full text-left px-3 py-2 text-xs border-b border-zinc-800/50 hover:bg-zinc-800/50 ${
                selected === f ? 'bg-zinc-800 text-zinc-200' : 'text-zinc-400'
              }`}
            >
              <FileText size={10} className="inline mr-1" />
              {f}
            </button>
          ))}
          {files.length === 0 && (
            <p className="text-xs text-zinc-600 p-3">将.md文件放入 data/knowledge/</p>
          )}
        </div>
      </div>
      <div className="flex-1 bg-zinc-900 rounded-lg border border-zinc-800 overflow-hidden flex flex-col">
        <div className="flex items-center justify-between px-4 py-2 border-b border-zinc-800">
          <span className="text-xs text-zinc-400 flex items-center gap-1">
            <Edit3 size={12} /> {selected || '选择文件'}
          </span>
          {content && <span className="text-xs text-zinc-600">{content.split('\n').length} 行</span>}
        </div>
        <div className="flex-1 overflow-auto p-4">
          {content ? (
            <div className="text-sm text-zinc-300 leading-relaxed" dangerouslySetInnerHTML={{ __html: renderMarkdown(content) }} />
          ) : (
            <div className="text-center text-zinc-600 mt-20 text-sm">选择知识文件查看</div>
          )}
        </div>
      </div>
    </div>
  )
}
