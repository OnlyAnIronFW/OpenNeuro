import { useState, useEffect, useCallback } from 'react'
import { Save, RotateCcw } from 'lucide-react'
import { CodeMirrorEditor } from '@/components/CodeMirrorEditor'

export function ConfigCenter() {
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(true)
  const [saved, setSaved] = useState(false)

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/config')
      const d = await r.json()
      setContent(d.content || '')
    } catch { setContent('# 加载失败') }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  const save = async () => {
    await fetch('/api/config', {
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
          <h2 className="text-sm font-medium text-zinc-200">配置中心</h2>
          <p className="text-xs text-zinc-500 mt-0.5">config.yaml — 所有参数。修改后自动热更新。</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={load}
            className="flex items-center gap-1 px-3 py-1.5 bg-zinc-800 text-zinc-300 rounded text-xs hover:bg-zinc-700 transition-colors"
          >
            <RotateCcw size={12} /> 重载
          </button>
          <button
            onClick={save}
            className={`flex items-center gap-1 px-3 py-1.5 rounded text-xs font-medium transition-colors ${
              saved ? 'bg-emerald-900/50 text-emerald-400' : 'bg-zinc-100 text-zinc-900 hover:bg-zinc-200'
            }`}
          >
            <Save size={12} /> {saved ? '已保存' : '保存'}
          </button>
        </div>
      </div>
      <CodeMirrorEditor
        value={content}
        onChange={setContent}
        language="yaml"
      />
    </div>
  )
}
