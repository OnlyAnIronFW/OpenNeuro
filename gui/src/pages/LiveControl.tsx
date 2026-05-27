import { useState, useRef, useEffect } from 'react'
import { useAppStore, ChatMessage } from '@/stores/appStore'
import { useApi } from '@/hooks/useApi'
import { Send, RotateCcw, Cpu, Radio } from 'lucide-react'

export function LiveControl() {
  const [input, setInput] = useState('')
  const [userName, setUserName] = useState('手操')
  const [sending, setSending] = useState(false)
  const chatEnd = useRef<HTMLDivElement>(null)
  const { messages, decisionLog, s1Mode, s2Mode, addMessage, addDecisionLog, clearMessages } = useAppStore()
  const { sendMessage, toggleS1, toggleS2, reset } = useApi()

  useEffect(() => { chatEnd.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  const handleSend = async () => {
    if (!input.trim() || sending) return
    const text = input.trim()
    setInput('')
    setSending(true)

    const msgId = Date.now().toString()
    addMessage({ id: msgId, user: userName, text, isBot: false, timestamp: Date.now() })
    addDecisionLog(`发送: [${userName}] ${text}`)

    const resp = await sendMessage(text, userName)
    if (resp.error) {
      addDecisionLog(`错误: ${resp.error}`)
    } else {
      addMessage({
        id: `r_${msgId}`,
        user: 'NewRoad',
        text: resp.reply || (resp.error ? `[错误] ${resp.error}` : '(空回复)'),
        isBot: true,
        timestamp: Date.now(),
        s1Token: resp.s1_token,
        s1Confidence: resp.s1_confidence,
        s2Latency: resp.total_latency_ms,
        cacheHit: resp.cache_hit,
      })
      addDecisionLog(
        `S1=${resp.s1_token} conf=${resp.s1_confidence} S2=${resp.s2_latency_ms?.toFixed(0)}ms cache=${resp.cache_hit} reply=${resp.reply?.slice(0, 30)}`
      )
    }
    setSending(false)
  }

  return (
    <div className="flex gap-4 h-full max-h-[calc(100vh-3rem)]">
      {/* 聊天面板 */}
      <div className="flex-1 flex flex-col bg-zinc-900 rounded-lg border border-zinc-800 overflow-hidden">
        {/* 工具栏 */}
        <div className="flex items-center gap-2 p-2 border-b border-zinc-800">
          <button
            onClick={toggleS1}
            className={`flex items-center gap-1 px-2 py-1 rounded text-xs transition-colors ${
              s1Mode === 'real' ? 'bg-emerald-900/50 text-emerald-400 border border-emerald-800' : 'bg-zinc-800 text-zinc-400'
            }`}
          >
            <Cpu size={12} /> S1:{s1Mode}
          </button>
          <button
            onClick={toggleS2}
            className={`flex items-center gap-1 px-2 py-1 rounded text-xs transition-colors ${
              s2Mode === 'real' ? 'bg-emerald-900/50 text-emerald-400 border border-emerald-800' : 'bg-zinc-800 text-zinc-400'
            }`}
          >
            <Radio size={12} /> S2:{s2Mode}
          </button>
          <div className="flex-1" />
          <button onClick={() => { clearMessages(); reset() }} className="p-1 text-zinc-500 hover:text-zinc-300" title="重置">
            <RotateCcw size={14} />
          </button>
        </div>

        {/* 消息列表 */}
        <div className="flex-1 overflow-auto p-3 space-y-3">
          {messages.length === 0 && (
            <div className="text-center text-zinc-600 mt-20">
              <p className="text-lg">AI 主播测试控制台</p>
              <p className="text-sm mt-1">在下方输入消息，模拟观众弹幕</p>
            </div>
          )}
          {messages.map((m) => (
            <MessageBubble key={m.id} msg={m} />
          ))}
          <div ref={chatEnd} />
        </div>

        {/* 输入区 */}
        <div className="p-3 border-t border-zinc-800">
          <div className="flex gap-2 mb-2">
            <input
              value={userName}
              onChange={(e) => setUserName(e.target.value)}
              className="w-24 bg-zinc-800 border border-zinc-700 rounded px-2 py-0.5 text-xs text-zinc-300"
              placeholder="用户名"
            />
          </div>
          <div className="flex gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSend()}
              placeholder="输入弹幕内容..."
              disabled={sending}
              className="flex-1 bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-zinc-600 disabled:opacity-50"
            />
            <button
              onClick={handleSend}
              disabled={sending || !input.trim()}
              className="px-4 py-2 bg-zinc-100 text-zinc-900 rounded-lg text-sm font-medium hover:bg-zinc-200 disabled:opacity-30 transition-colors flex items-center gap-1"
            >
              <Send size={14} /> 发送
            </button>
          </div>
        </div>
      </div>

      {/* 决策追踪面板 */}
      <div className="w-80 bg-zinc-900 rounded-lg border border-zinc-800 overflow-hidden flex flex-col shrink-0">
        <div className="p-2 border-b border-zinc-800 text-xs font-medium text-zinc-400">决策追踪</div>
        <div className="flex-1 overflow-auto p-2 font-mono text-xs space-y-0.5">
          {decisionLog.length === 0 && (
            <p className="text-zinc-600">等待消息...</p>
          )}
          {decisionLog.map((line, i) => (
            <div key={i} className="text-zinc-400 break-all leading-relaxed">
              {line}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function MessageBubble({ msg }: { msg: ChatMessage }) {
  return (
    <div className={`flex ${msg.isBot ? 'justify-start' : 'justify-end'}`}>
      <div className={`max-w-[80%] rounded-lg px-3 py-2 text-sm ${
        msg.isBot
          ? 'bg-zinc-800 text-zinc-200'
          : 'bg-blue-900/40 text-blue-200'
      }`}>
        <div className="text-xs text-zinc-500 mb-0.5">
          {msg.user} {msg.isBot && msg.s1Token && `· ${msg.s1Token}`}
          {msg.s2Latency != null && msg.s2Latency > 0 && ` · ${msg.s2Latency.toFixed(0)}ms`}
          {msg.cacheHit && ' · 缓存命中'}
        </div>
        <div className="whitespace-pre-wrap break-words">{msg.text}</div>
      </div>
    </div>
  )
}
