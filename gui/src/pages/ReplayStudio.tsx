import { useState, useEffect, useCallback } from 'react'
import { RefreshCw, FileText, FileX } from 'lucide-react'

interface Recording {
  file: string; size_kb: number; date: string
}

export function ReplayStudio() {
  const [recordings, setRecordings] = useState<Recording[]>([])
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch('/api/recordings')
      setRecordings((await r.json()).files || [])
    } catch {}
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <div className="space-y-4 max-h-[calc(100vh-3rem)] overflow-auto">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-medium text-zinc-200">录制回放</h2>
          <p className="text-xs text-zinc-500 mt-0.5">.rec 录制文件管理</p>
        </div>
        <button onClick={load} disabled={loading} className="p-2 text-zinc-400 hover:text-zinc-200">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      <div className="bg-zinc-900 rounded-lg border border-zinc-800">
        {recordings.length === 0 ? (
          <div className="text-center text-zinc-600 py-12 text-sm">
            <FileText size={32} className="mx-auto mb-2 opacity-50" />
            暂无录制文件。直播时自动录制。
          </div>
        ) : (
          <div className="divide-y divide-zinc-800">
            {recordings.map((rec, i) => (
              <div key={i} className="flex items-center justify-between px-4 py-3 hover:bg-zinc-800/50">
                <div>
                  <div className="text-sm text-zinc-300">{rec.file}</div>
                  <div className="text-xs text-zinc-500">{rec.date} · {rec.size_kb}KB</div>
                </div>
                <button
                  disabled
                  className="flex items-center gap-1 px-3 py-1.5 bg-zinc-800 rounded text-xs text-zinc-500 cursor-not-allowed"
                  title="回放引擎尚未实现"
                >
                  <FileX size={12} /> 暂不可用
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
