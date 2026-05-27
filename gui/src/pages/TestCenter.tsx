import { useState } from 'react'
import { Play, Terminal, FileText } from 'lucide-react'

const SCENARIOS = [
  { id: 'welcome', label: '开播欢迎', desc: '模拟观众进入打招呼' },
  { id: 'high_velocity', label: '弹幕洪流', desc: '100条/分钟高速弹幕' },
  { id: 'silent', label: '冷场测试', desc: '0.1条/分钟极限冷场' },
  { id: 'toxic', label: '恶意弹幕', desc: '压力测试黑子言论' },
  { id: 'mixed_lang', label: '多语言', desc: '中英日混合输入' },
]

export function TestCenter() {
  const [running, setRunning] = useState('')
  const [log, setLog] = useState<string[]>([])

  const runScenario = async (id: string) => {
    setRunning(id)
    setLog(prev => [...prev, `[${new Date().toLocaleTimeString()}] 启动场景: ${id}`])
    try {
      const r = await fetch(`/api/test/run/${id}`, { method: 'POST' })
      const d = await r.json()
      setLog(prev => [...prev, `[${new Date().toLocaleTimeString()}] 完成: ${d.status || 'ok'}`])
    } catch {
      setLog(prev => [...prev, `[${new Date().toLocaleTimeString()}] 错误: 后端不可达`])
    }
    setRunning('')
  }

  return (
    <div className="space-y-4 max-h-[calc(100vh-3rem)] overflow-auto">
      <div>
        <h2 className="text-sm font-medium text-zinc-200">测试中心</h2>
        <p className="text-xs text-zinc-500 mt-0.5">场景模拟 + 压力测试</p>
      </div>

      <div className="grid grid-cols-2 gap-3">
        {SCENARIOS.map(s => (
          <button
            key={s.id}
            onClick={() => runScenario(s.id)}
            disabled={running !== ''}
            className="bg-zinc-900 border border-zinc-800 rounded-lg p-4 text-left hover:border-zinc-700 disabled:opacity-50"
          >
            <div className="flex items-center gap-2 mb-1">
              <Play size={14} className={running === s.id ? 'text-emerald-400 animate-pulse' : 'text-zinc-500'} />
              <span className="text-sm text-zinc-200">{s.label}</span>
            </div>
            <p className="text-xs text-zinc-500">{s.desc}</p>
          </button>
        ))}
      </div>

      <div className="bg-zinc-950 rounded-lg border border-zinc-800 p-3">
        <div className="flex items-center gap-2 mb-2">
          <Terminal size={14} className="text-zinc-500" />
          <span className="text-xs text-zinc-400">测试日志</span>
        </div>
        <div className="font-mono text-xs space-y-0.5 max-h-60 overflow-auto">
          {log.length === 0 ? (
            <p className="text-zinc-600">点击场景开始测试</p>
          ) : (
            log.map((l, i) => <div key={i} className="text-zinc-400">{l}</div>)
          )}
        </div>
      </div>
    </div>
  )
}
