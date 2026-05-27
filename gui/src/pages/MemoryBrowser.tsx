import { useState, useEffect, useCallback } from 'react'
import { Brain, Users, MessageSquare, RefreshCw } from 'lucide-react'

interface ViewerData {
  user_id: string; display_name: string; platform: string
  interaction_count: number; loyalty_level: number
  first_seen: number; last_seen: number
  topics: string[]; known_facts: Record<string,string>
  interaction_style: string
}

export function MemoryBrowser() {
  const [viewers, setViewers] = useState<ViewerData[]>([])
  const [selected, setSelected] = useState<ViewerData | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch('/api/memory/viewers')
      const d = await r.json()
      setViewers(d.viewers || [])
    } catch {}
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  const loyaltyLabel = (l: number) => ['路人','常客','老粉','铁粉'][l] || '路人'

  return (
    <div className="flex gap-4 h-full max-h-[calc(100vh-3rem)]">
      {/* 观众列表 */}
      <div className="w-72 bg-zinc-900 rounded-lg border border-zinc-800 flex flex-col shrink-0">
        <div className="flex items-center justify-between p-2 border-b border-zinc-800">
          <span className="text-xs font-medium text-zinc-300 flex items-center gap-1">
            <Brain size={14} /> 观众档案 ({viewers.length})
          </span>
          <button onClick={load} disabled={loading} className="p-1 text-zinc-500 hover:text-zinc-300">
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
        <div className="flex-1 overflow-auto">
          {viewers.map((v) => (
            <button
              key={v.user_id}
              onClick={() => setSelected(v)}
              className={`w-full text-left px-3 py-2 text-xs border-b border-zinc-800/50 hover:bg-zinc-800/50 transition-colors ${
                selected?.user_id === v.user_id ? 'bg-zinc-800' : ''
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="text-zinc-200 font-medium">{v.display_name || v.user_id}</span>
                <span className={`text-[10px] px-1 rounded ${
                  v.loyalty_level >= 3 ? 'bg-amber-900/50 text-amber-400' :
                  v.loyalty_level >= 2 ? 'bg-blue-900/50 text-blue-400' :
                  v.loyalty_level >= 1 ? 'bg-zinc-700 text-zinc-400' :
                  'bg-zinc-800 text-zinc-600'
                }`}>
                  {loyaltyLabel(v.loyalty_level)}
                </span>
              </div>
              <div className="text-zinc-500 mt-0.5">
                互动{v.interaction_count}次 · {v.platform || '未知平台'}
              </div>
            </button>
          ))}
          {viewers.length === 0 && (
            <div className="text-center text-zinc-600 mt-10 text-xs">暂无观众数据</div>
          )}
        </div>
      </div>

      {/* 详情 */}
      <div className="flex-1 bg-zinc-900 rounded-lg border border-zinc-800 p-4 overflow-auto">
        {selected ? (
          <div className="space-y-4 text-sm">
            <div>
              <h3 className="text-zinc-200 font-medium text-lg">{selected.display_name}</h3>
              <p className="text-zinc-500 text-xs">ID: {selected.user_id} · {loyaltyLabel(selected.loyalty_level)}</p>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Stat label="互动次数" value={String(selected.interaction_count)} />
              <Stat label="平台" value={selected.platform || '-'} />
              <Stat label="首次出现" value={selected.first_seen ? new Date(selected.first_seen*1000).toLocaleDateString() : '-'} />
              <Stat label="最近活跃" value={selected.last_seen ? new Date(selected.last_seen*1000).toLocaleDateString() : '-'} />
            </div>
            {selected.topics.length > 0 && (
              <div>
                <h4 className="text-xs text-zinc-500 mb-1">常聊话题</h4>
                <div className="flex flex-wrap gap-1">
                  {selected.topics.map((t,i) => (
                    <span key={i} className="px-2 py-0.5 bg-zinc-800 rounded text-xs text-zinc-300">{t}</span>
                  ))}
                </div>
              </div>
            )}
            {Object.keys(selected.known_facts || {}).length > 0 && (
              <div>
                <h4 className="text-xs text-zinc-500 mb-1">已知信息</h4>
                <div className="space-y-1">
                  {Object.entries(selected.known_facts).map(([k,v]) => (
                    <div key={k} className="text-xs"><span className="text-zinc-500">{k}:</span> <span className="text-zinc-300">{v}</span></div>
                  ))}
                </div>
              </div>
            )}
            {selected.interaction_style && (
              <div>
                <h4 className="text-xs text-zinc-500 mb-1">互动偏好</h4>
                <p className="text-zinc-300 text-xs">{selected.interaction_style}</p>
              </div>
            )}
          </div>
        ) : (
          <div className="text-center text-zinc-600 mt-20">
            <Users size={32} className="mx-auto mb-2 opacity-50" />
            <p>选择一个观众查看详情</p>
          </div>
        )}
      </div>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-zinc-800/50 rounded p-2">
      <div className="text-xs text-zinc-500">{label}</div>
      <div className="text-zinc-200 text-sm mt-0.5">{value}</div>
    </div>
  )
}
