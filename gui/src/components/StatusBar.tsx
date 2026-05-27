import { useAppStore } from '@/stores/appStore'
import { Wifi, WifiOff } from 'lucide-react'

export function StatusBar({ connected }: { connected: boolean }) {
  const { s1Mode, s2Mode, replyCount, cacheHitRate } = useAppStore()

  return (
    <footer className="h-7 bg-zinc-900 border-t border-zinc-800 flex items-center px-4 gap-4 text-xs text-zinc-500 shrink-0">
      <span className="flex items-center gap-1">
        {connected ? <Wifi size={12} className="text-emerald-500" /> : <WifiOff size={12} className="text-red-500" />}
        {connected ? '已连接' : '未连接'}
      </span>
      <span>S1: {s1Mode}</span>
      <span>S2: {s2Mode}</span>
      <span>回复: {replyCount}</span>
      <span>缓存: {(cacheHitRate * 100).toFixed(0)}%</span>
    </footer>
  )
}
