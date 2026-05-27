import { useState, useEffect, useCallback } from 'react'
import { Save, RotateCcw, Eye, EyeOff } from 'lucide-react'
import { CodeMirrorEditor } from '@/components/CodeMirrorEditor'

function renderMarkdown(text: string): string {
  const lines = text.split('\n')
  const out: string[] = []
  let i = 0

  while (i < lines.length) {
    const line = lines[i]

    // Fenced code blocks
    if (line.trimStart().startsWith('```')) {
      const fence = line.match(/^(\s*)(`{3,})\s*(\S*)/)
      if (fence) {
        const indent = fence[1]
        const lang = fence[3]
        const codeLines: string[] = []
        i++
        while (i < lines.length && !lines[i].trimStart().startsWith('```')) {
          codeLines.push(lines[i])
          i++
        }
        i++ // skip closing fence
        let code = codeLines.join('\n')
        // dedent relative to the opening fence
        if (indent) {
          const re = new RegExp('^' + indent)
          code = code.split('\n').map(l => l.replace(re, '')).join('\n')
        }
        out.push(`<pre class="bg-zinc-950 p-3 rounded text-xs text-zinc-300 overflow-auto${lang ? ' language-' + lang : ''}"><code>${escapeHtml(code)}</code></pre>`)
        continue
      }
    }

    if (line.trim() === '') {
      out.push('')
      i++
      continue
    }

    // Headings
    const h1 = line.match(/^(#{1,6})\s+(.+)/)
    if (h1) {
      const level = h1[1].length
      const sizes = ['', 'text-xl font-bold', 'text-lg font-semibold', 'text-base font-semibold', 'text-sm font-medium', 'text-sm font-medium', 'text-xs font-medium']
      const mb = ['', 'mb-2', 'mb-1.5', 'mb-1', 'mb-1', 'mb-0.5', 'mb-0.5']
      out.push(`<h${level} class="${sizes[level]} text-zinc-200 ${mb[level]}">${inlineMarkdown(h1[2])}</h${level}>`)
      i++
      continue
    }

    // Unordered list
    const listItem = line.match(/^[-*]\s+(.+)/)
    if (listItem) {
      out.push('<ul class="mb-1 space-y-0.5">')
      while (i < lines.length) {
        const li = lines[i].match(/^[-*]\s+(.+)/)
        if (!li) break
        out.push(`<li class="text-zinc-300 ml-4 list-disc">${inlineMarkdown(li[1])}</li>`)
        i++
      }
      out.push('</ul>')
      continue
    }

    // Regular paragraph line
    out.push(`<p class="text-zinc-300 mb-1">${inlineMarkdown(line)}</p>`)
    i++
  }

  return out.join('\n')
}

const INLINE_RE = /(\*\*(.+?)\*\*)|(\*(.+?)\*)|(`{1,2})(.+?)\5/g

function inlineMarkdown(text: string): string {
  return escapeHtml(text).replace(
    INLINE_RE,
    (_, __, bold, ___, italic, ____, code) => {
      if (bold) return `<strong>${bold}</strong>`
      if (italic) return `<em>${italic}</em>`
      if (code) return `<code class="bg-zinc-950 px-1 rounded text-emerald-400">${code}</code>`
      return _
    }
  )
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

export function PersonaEditor() {
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(true)
  const [saved, setSaved] = useState(false)
  const [showPreview, setShowPreview] = useState(false)

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/persona')
      const d = await r.json()
      setContent(d.content || '')
    } catch { setContent('// 加载失败') }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  const save = async () => {
    await fetch('/api/persona', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    })
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  if (loading) return <div className="text-zinc-400 p-4">加载中...</div>

  return (
    <div className="flex flex-col h-full max-h-[calc(100vh-3rem)]">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h2 className="text-sm font-medium text-zinc-200">人设核心档案</h2>
          <p className="text-xs text-zinc-500 mt-0.5">persona_core.md — 唯一的人设真相源。保存后自动提取到 S1/S2 人设层。</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowPreview(s => !s)}
            className="flex items-center gap-1 px-3 py-1.5 bg-zinc-800 text-zinc-300 rounded text-xs hover:bg-zinc-700 transition-colors"
            title={showPreview ? '关闭预览' : '预览'}
          >
            {showPreview ? <EyeOff size={12} /> : <Eye size={12} />}
            {showPreview ? '编辑' : '预览'}
          </button>
          <button onClick={load} className="flex items-center gap-1 px-3 py-1.5 bg-zinc-800 text-zinc-300 rounded text-xs hover:bg-zinc-700 transition-colors">
            <RotateCcw size={12} /> 重载
          </button>
          <button onClick={save} className={`flex items-center gap-1 px-3 py-1.5 rounded text-xs font-medium transition-colors ${
            saved ? 'bg-emerald-900/50 text-emerald-400' : 'bg-zinc-100 text-zinc-900 hover:bg-zinc-200'
          }`}>
            <Save size={12} /> {saved ? '已保存' : '保存'}
          </button>
        </div>
      </div>

      <div className={`flex-1 flex gap-4 min-h-0 ${showPreview ? '' : ''}`}>
        {/* Editor */}
        <div className={`bg-zinc-900 rounded-lg border border-zinc-800 overflow-hidden ${showPreview ? 'flex-1 w-1/2' : 'flex-1 w-full'}`}>
          <CodeMirrorEditor
            value={content}
            onChange={(val) => { setContent(val); setSaved(false) }}
            language="markdown"
          />
        </div>

        {/* Preview */}
        {showPreview && (
          <div className="flex-1 w-1/2 bg-zinc-950 rounded-lg border border-zinc-800 p-4 overflow-auto">
            <div
              className="prose prose-invert prose-sm max-w-none"
              dangerouslySetInnerHTML={{ __html: renderMarkdown(content) }}
            />
          </div>
        )}
      </div>
    </div>
  )
}
