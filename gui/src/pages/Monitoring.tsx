import { useAppStore } from '@/stores/appStore'

export function Monitoring() {
  const { replyCount, cacheHitRate, s1Mode, s2Mode, connected, decisionLog, messages, personaName } = useAppStore()

  return (
    <div className="space-y-4 max-h-[calc(100vh-3rem)] overflow-auto">
      <h2 className="text-sm font-medium text-zinc-200">系统监控</h2>

      <div className="grid grid-cols-4 gap-3">
        <StatCard label="连接状态" value={connected ? '已连接' : '未连接'} color={connected ? 'emerald' : 'red'} />
        <StatCard label="S1 模式" value={s1Mode} color={s1Mode === 'real' ? 'emerald' : 'zinc'} />
        <StatCard label="S2 模式" value={s2Mode} color={s2Mode === 'real' ? 'emerald' : 'zinc'} />
        <StatCard label="缓存命中率" value={`${(cacheHitRate * 100).toFixed(0)}%`} color="blue" />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <StatCard label="总回复数" value={String(replyCount)} color="zinc" />
        <StatCard label="缓冲消息" value={String(messages.length)} color="zinc" />
        <StatCard label="决策日志条目" value={String(decisionLog.length)} color="zinc" />
        <StatCard label="当前人设" value={personaName} color="amber" />
      </div>

      <div className="bg-zinc-900 rounded-lg border border-zinc-800 p-3">
        <h3 className="text-xs font-medium text-zinc-400 mb-2">决策日志 (最近 50 条)</h3>
        <div className="font-mono text-xs space-y-0.5 max-h-80 overflow-auto">
          {decisionLog.slice(-50).map((line, i) => (
            <div key={i} className="text-zinc-500 break-all">{line}</div>
          ))}
          {decisionLog.length === 0 && <p className="text-zinc-600">暂无日志</p>}
        </div>
      </div>
    </div>
  )
}

function StatCard({ label, value, color }: { label: string; value: string; color: string }) {
  const colors: Record<string, string> = {
    emerald: 'border-emerald-800 bg-emerald-950/30 text-emerald-400',
    red: 'border-red-800 bg-red-950/30 text-red-400',
    blue: 'border-blue-800 bg-blue-950/30 text-blue-400',
    amber: 'border-amber-800 bg-amber-950/30 text-amber-400',
    zinc: 'border-zinc-800 bg-zinc-900/50 text-zinc-300',
  }
  return (
    <div className={`rounded-lg border px-3 py-2.5 ${colors[color] || colors.zinc}`}>
      <div className="text-xs text-zinc-500">{label}</div>
      <div className="text-lg font-semibold mt-0.5">{value}</div>
    </div>
  )
}
