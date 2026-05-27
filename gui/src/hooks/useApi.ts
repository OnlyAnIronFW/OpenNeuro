import { useCallback, useEffect, useRef } from 'react'
import { useAppStore } from '@/stores/appStore'

const BASE = '/api'

export function useApi() {
  const setStatus = useAppStore((s) => s.setStatus)
  const setConnected = useAppStore((s) => s.setConnected)
  const pollRef = useRef<ReturnType<typeof setInterval>>()

  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch(`${BASE}/status`)
      if (r.ok) {
        const data = await r.json()
        setConnected(true)
        setStatus(data)
      }
    } catch {
      setConnected(false)
    }
  }, [setStatus, setConnected])

  useEffect(() => {
    fetchStatus()
    pollRef.current = setInterval(fetchStatus, 5000)
    return () => clearInterval(pollRef.current)
  }, [fetchStatus])

  const sendMessage = useCallback(async (text: string, user = '手操') => {
    const r = await fetch(`${BASE}/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, user, mentioned_bot: true }),
    })
    return r.ok ? await r.json() : { error: `HTTP ${r.status}` }
  }, [])

  const toggleS1 = useCallback(async () => {
    const r = await fetch(`${BASE}/toggle_s1`, { method: 'POST' })
    if (r.ok) setStatus(await r.json())
  }, [setStatus])

  const toggleS2 = useCallback(async () => {
    const r = await fetch(`${BASE}/toggle_s2`, { method: 'POST' })
    if (r.ok) setStatus(await r.json())
  }, [setStatus])

  const reset = useCallback(async () => {
    await fetch(`${BASE}/reset`, { method: 'POST' })
  }, [])

  return { fetchStatus, sendMessage, toggleS1, toggleS2, reset }
}
