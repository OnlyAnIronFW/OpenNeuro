import { useState } from 'react'
import { useAppStore } from '@/stores/appStore'
import { Sidebar } from '@/components/Sidebar'
import { StatusBar } from '@/components/StatusBar'
import { LiveControl } from '@/pages/LiveControl'
import { PersonaEditor } from '@/pages/PersonaEditor'
import { ConfigCenter } from '@/pages/ConfigCenter'
import { Monitoring } from '@/pages/Monitoring'
import { LogViewer } from '@/pages/LogViewer'
import { MemoryBrowser } from '@/pages/MemoryBrowser'
import { ReplayStudio } from '@/pages/ReplayStudio'
import { IterationHub } from '@/pages/IterationHub'
import { TestCenter } from '@/pages/TestCenter'
import { KnowledgeBase } from '@/pages/KnowledgeBase'
import { SkillLibrary } from '@/pages/SkillLibrary'
import { Settings } from '@/pages/Settings'

type Page = 'live' | 'persona' | 'memory' | 'skills' | 'knowledge' |
            'replay' | 'iteration' | 'test' | 'config' | 'monitor' | 'logs' | 'settings'

export default function App() {
  const [page, setPage] = useState<Page>('live')
  const connected = useAppStore((s) => s.connected)

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar page={page} onNavigate={setPage} />
      <main className="flex-1 flex flex-col overflow-hidden">
        <div className="flex-1 overflow-auto p-4">
          {page === 'live' && <LiveControl />}
          {page === 'persona' && <PersonaEditor />}
          {page === 'config' && <ConfigCenter />}
          {page === 'monitor' && <Monitoring />}
          {page === 'logs' && <LogViewer />}
          {page === 'memory' && <MemoryBrowser />}
          {page === 'replay' && <ReplayStudio />}
          {page === 'iteration' && <IterationHub />}
          {page === 'test' && <TestCenter />}
          {page === 'knowledge' && <KnowledgeBase />}
          {page === 'skills' && <SkillLibrary />}
          {page === 'settings' && <Settings />}
        </div>
        <StatusBar connected={connected} />
      </main>
    </div>
  )
}
