import { useState, useEffect, useCallback } from 'react'
import { RefreshCw, Check, X, Zap } from 'lucide-react'

interface InjectionRecord {
  timestamp: number; approved_by: string; rule_count: number
  rules: { type: string; desc: string; conf: number }[]
  message: string
}

export function IterationHub() {
  const [history, setHistory] = useState<InjectionRecord[]>([])
  const [stats, setStats] = useState<Record<string,any>>({})

  const load = useCallback(async () => {
    try {
      const [hr, sr] = await Promise.all([
        fetch('/api/iteration/history'),
        fetch('/api/iteration/stats'),
      ])
      setHistory((await hr.json()).history || [])
      setStats((await sr.json()))
    } catch {}
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <div className="space-y-4 max-h-[calc(100vh-3rem)] overflow-auto">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-medium text-zinc-200">自迭代中心</h2>
          <p className="text-xs text-zinc-500 mt-0.5">Phase 2-4: 评分 → 提炼 → 注入</p>
        </div>
        <button onClick={load} className="p-2 text-zinc-400 hover:text-zinc-200">
          <RefreshCw size={14} />
        </button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-4 gap-3">
        {[
          ['总注入次数', stats.injection_count || 0],
          ['训练样本', stats.training_samples || 0],
          ['已修正', stats.training_corrected || 0],
          ['待审核', stats.training_pending || 0],
        ].map(([label, value]) => (
          <div key={label} className="bg-zinc-900 rounded-lg border border-zinc-800 p-3">
            <div className="text-xs text-zinc-500">{label}</div>
            <div className="text-lg font-semibold text-zinc-200 mt-0.5">{String(value)}</div>
          </div>
        ))}
      </div>

      {/* History */}
      <div className="bg-zinc-900 rounded-lg border border-zinc-800">
        <div className="p-3 border-b border-zinc-800 text-xs font-medium text-zinc-400">
          注入历史
        </div>
        {history.length === 0 ? (
          <div className="text-center text-zinc-600 py-8 text-sm">暂无注入记录</div>
        ) : (
          <div className="divide-y divide-zinc-800">
            {history.map((h, i) => (
              <div key={i} className="p-3">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs text-zinc-300">
                    {new Date(h.timestamp * 1000).toLocaleDateString()} · {h.approved_by} · {h.rule_count}条规则
                  </span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-900/50 text-emerald-400">
                    已应用
                  </span>
                </div>
                {h.rules.slice(0, 3).map((r, j) => (
                  <div key={j} className="text-xs text-zinc-500 ml-2">
                    [{r.type}] {r.desc.slice(0, 60)} (conf={r.conf.toFixed(2)})
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
