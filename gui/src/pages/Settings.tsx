import { useState } from 'react'
import { Settings as SettingsIcon, Key, Cpu, Radio, Save } from 'lucide-react'

export function Settings() {
  const [s1Mode, setS1Mode] = useState('real')
  const [s2Mode, setS2Mode] = useState('real')
  const [saved, setSaved] = useState(false)

  const toggleS1 = async () => {
    try {
      const r = await fetch('/api/toggle_s1', { method: 'POST' })
      setS1Mode((await r.json()).s1Mode || 'mock')
    } catch {}
  }

  const toggleS2 = async () => {
    try {
      const r = await fetch('/api/toggle_s2', { method: 'POST' })
      setS2Mode((await r.json()).s2Mode || 'mock')
    } catch {}
  }

  return (
    <div className="max-w-lg space-y-4 max-h-[calc(100vh-3rem)] overflow-auto">
      <h2 className="text-sm font-medium text-zinc-200 flex items-center gap-1.5">
        <SettingsIcon size={14} /> 系统设置
      </h2>

      <div className="bg-zinc-900 rounded-lg border border-zinc-800 p-4 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Cpu size={14} className="text-zinc-500" />
            <div>
              <div className="text-sm text-zinc-300">S1 模型 (MiniCPM)</div>
              <div className="text-xs text-zinc-500">实时决策引擎 · localhost:9060</div>
            </div>
          </div>
          <button
            onClick={toggleS1}
            className={`px-3 py-1 rounded text-xs ${
              s1Mode === 'real' ? 'bg-emerald-900/50 text-emerald-400 border border-emerald-800' : 'bg-zinc-800 text-zinc-400'
            }`}
          >
            {s1Mode === 'real' ? '真实' : 'Mock'}
          </button>
        </div>

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Radio size={14} className="text-zinc-500" />
            <div>
              <div className="text-sm text-zinc-300">S2 模型 (DeepSeek)</div>
              <div className="text-xs text-zinc-500">深度回复生成 · api.deepseek.com</div>
            </div>
          </div>
          <button
            onClick={toggleS2}
            className={`px-3 py-1 rounded text-xs ${
              s2Mode === 'real' ? 'bg-emerald-900/50 text-emerald-400 border border-emerald-800' : 'bg-zinc-800 text-zinc-400'
            }`}
          >
            {s2Mode === 'real' ? '真实' : 'Mock'}
          </button>
        </div>

        <div className="flex items-center justify-between pt-3 border-t border-zinc-800">
          <div className="flex items-center gap-2">
            <Key size={14} className="text-zinc-500" />
            <div>
              <div className="text-sm text-zinc-300">DeepSeek API Key</div>
              <div className="text-xs text-zinc-500">环境变量已配置</div>
            </div>
          </div>
          <span className="text-xs text-emerald-400">已设置</span>
        </div>
      </div>

      <div className="bg-zinc-900 rounded-lg border border-zinc-800 p-4">
        <div className="text-xs text-zinc-500 space-y-1">
          <p><strong className="text-zinc-400">项目路径:</strong> F:\OpenNeuro</p>
          <p><strong className="text-zinc-400">模型:</strong> MiniCPM-o 4.5 Q4_K_M · DeepSeek V4 Flash</p>
          <p><strong className="text-zinc-400">B站弹幕:</strong> MaiBot Live Hub (ws://127.0.0.1:18190/ws)</p>
          <p><strong className="text-zinc-400">GUI:</strong> Electron/React/TailwindCSS</p>
        </div>
      </div>
    </div>
  )
}
