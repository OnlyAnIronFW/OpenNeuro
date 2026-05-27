import { useState, useEffect, useCallback } from 'react'
import { ScrollText, RefreshCw } from 'lucide-react'

interface LogEntry {
  ts: string
  module: string
  level: string
  msg: string
  extra?: Record<string, any>
}

export function LogViewer() {
  const [modules, setModules] = useState<string[]>([])
  const [selected, setSelected] = useState('main')
  const [entries, setEntries] = useState<LogEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [autoRefresh, setAutoRefresh] = useState(true)

  const loadModules = useCallback(async () => {
    try {
      const r = await fetch('/api/logs')
      const d = await r.json()
      setModules(d.modules || [])
    } catch {}
  }, [])

  const loadLogs = useCallback(async (mod: string) => {
    setLoading(true)
    try {
      const r = await fetch(`/api/logs/${mod}?lines=500`)
      const d = await r.json()
      setEntries(d.entries || [])
    } catch {}
    setLoading(false)
  }, [])

  useEffect(() => { loadModules() }, [loadModules])
  useEffect(() => { loadLogs(selected) }, [selected, loadLogs])
  useEffect(() => {
    if (!autoRefresh) return
    const t = setInterval(() => loadLogs(selected), 5000)
    return () => clearInterval(t)
  }, [autoRefresh, selected, loadLogs])

  const levelColor = (l: string) => {
    switch (l) {
      case 'ERROR': return 'text-red-400 bg-red-950/30'
      case 'WARN': return 'text-yellow-400 bg-yellow-950/30'
      case 'DEBUG': return 'text-zinc-500 bg-zinc-800/50'
      default: return 'text-zinc-300'
    }
  }

  return (
    <div className="flex flex-col h-full max-h-[calc(100vh-3rem)]">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-medium text-zinc-200 flex items-center gap-1.5">
            <ScrollText size={14} /> 系统日志
          </h2>
          <div className="flex gap-1">
            {modules.map((m) => (
              <button
                key={m}
                onClick={() => setSelected(m)}
                className={`px-2 py-0.5 rounded text-xs transition-colors ${
                  selected === m
                    ? 'bg-zinc-200 text-zinc-900'
                    : 'bg-zinc-800 text-zinc-400 hover:text-zinc-200'
                }`}
              >
                {m}
              </button>
            ))}
            {modules.length === 0 && (
              <span className="text-xs text-zinc-600">暂无日志模块</span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1 text-xs text-zinc-500">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="rounded"
            />
            自动刷新
          </label>
          <button
            onClick={() => loadLogs(selected)}
            disabled={loading}
            className="p-1 text-zinc-500 hover:text-zinc-300 disabled:opacity-30"
            title="刷新"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>
      <div className="flex-1 bg-zinc-950 rounded-lg border border-zinc-800 overflow-auto font-mono text-xs">
        {entries.length === 0 && (
          <div className="text-center text-zinc-600 mt-20">暂无日志。发送一条消息试试。</div>
        )}
        {entries.map((e, i) => (
          <div
            key={i}
            className={`flex items-start gap-2 px-3 py-0.5 border-b border-zinc-900 hover:bg-zinc-900/50 ${
              e.level === 'ERROR' ? 'bg-red-950/10' : ''
            }`}
          >
            <span className="text-zinc-600 shrink-0 w-16">{e.ts?.slice(11, 23) || ''}</span>
            <span className="text-zinc-500 shrink-0 w-14">{e.module}</span>
            <span className={`shrink-0 w-10 px-1 rounded text-center text-[10px] font-medium ${levelColor(e.level)}`}>
              {e.level}
            </span>
            <span className={`break-all ${e.level === 'ERROR' ? 'text-red-300' : 'text-zinc-300'}`}>
              {e.msg}
            </span>
            {e.extra && Object.keys(e.extra).length > 0 && (
              <span className="text-zinc-600 ml-1">
                {Object.entries(e.extra).map(([k, v]) => `${k}=${v}`).join(' ')}
              </span>
            )}
          </div>
        ))}
      </div>
      <div className="text-xs text-zinc-600 mt-1.5">
        {entries.length} 条 · 日志路径: data/logs/{selected}.log
      </div>
    </div>
  )
}
