import { create } from 'zustand'

export interface ChatMessage {
  id: string
  user: string
  text: string
  isBot: boolean
  timestamp: number
  s1Token?: string
  s1Confidence?: number
  s2Latency?: number
  cacheHit?: boolean
}

export interface AppState {
  connected: boolean
  s1Mode: string
  s2Mode: string
  replyCount: number
  cacheHitRate: number
  personaName: string
  messages: ChatMessage[]
  decisionLog: string[]

  setConnected: (v: boolean) => void
  setStatus: (s: Partial<AppState>) => void
  addMessage: (m: ChatMessage) => void
  addDecisionLog: (line: string) => void
  clearMessages: () => void
}

export const useAppStore = create<AppState>((set) => ({
  connected: false,
  s1Mode: 'mock',
  s2Mode: 'mock',
  replyCount: 0,
  cacheHitRate: 0,
  personaName: 'NewRoad',
  messages: [],
  decisionLog: [],

  setConnected: (v) => set({ connected: v }),
  setStatus: (s) => set(s),
  addMessage: (m) => set((state) => ({ messages: [...state.messages, m] })),
  addDecisionLog: (line) =>
    set((state) => ({
      decisionLog: [...state.decisionLog.slice(-200), `[${new Date().toLocaleTimeString()}] ${line}`],
    })),
  clearMessages: () => set({ messages: [], decisionLog: [] }),
}))
