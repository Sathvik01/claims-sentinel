import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { NavLink, Route, Routes } from 'react-router-dom'
import { api, getRole } from './api'
import { useMeta } from './components/ui'
import AlertDetail from './pages/AlertDetail'
import Alerts from './pages/Alerts'
import Dashboard from './pages/Dashboard'
import Documents from './pages/Documents'
import Ingestion from './pages/Ingestion'
import Quality from './pages/Quality'

const NAV = [
  { to: '/', code: '01', label: 'Overview' },
  { to: '/alerts', code: '02', label: 'Alerts' },
  { to: '/ingestion', code: '03', label: 'Data ingestion' },
  { to: '/documents', code: '04', label: 'Documents' },
  { to: '/quality', code: '05', label: 'Detection quality' },
]

interface Run { id: number; finished_at: string | null; claims_analyzed: number; findings_count: number }

function StatusLine({ k, v, ok }: { k: string; v: React.ReactNode; ok?: boolean }) {
  return (
    <div className="grid grid-cols-[64px_minmax(0,1fr)] gap-2 py-0.5">
      <span className="text-rail-ink-2">{k}</span>
      <span className={`truncate ${ok === false ? 'text-rail-ink-2' : 'text-rail-ink'}`}>{v}</span>
    </div>
  )
}

export default function App() {
  const { data: meta } = useMeta()
  const run = useQuery({ queryKey: ['run'], queryFn: () => api.get<Run | null>('/api/analysis/latest'), refetchInterval: 15000 })
  const [role, setRole] = useState(getRole())
  const changeRole = (r: string) => {
    try { localStorage.setItem('role', r) } catch { /* private mode: role resets on reload */ }
    setRole(r)
  }
  const r = run.data
  return (
    <div className="min-h-screen md:grid md:grid-cols-[228px_minmax(0,1fr)]">
      <aside className="flex flex-col bg-rail text-rail-ink md:sticky md:top-0 md:h-screen">
        <div className="border-b border-rail-line px-4 py-4">
          <div className="flex items-center gap-2.5">
            <span className="grid h-8 w-8 place-items-center border border-rail-ink font-mono text-[12px] font-semibold">CS</span>
            <div>
              <div className="font-serif text-[16px] font-semibold leading-none">Claims Sentinel</div>
              <div className="mt-1 font-mono text-[10px] uppercase tracking-[0.08em] text-rail-ink-2">Health claims · L1</div>
            </div>
          </div>
        </div>
        <nav className="flex gap-0 overflow-x-auto md:block md:py-2">
          {NAV.map(n => (
            <NavLink key={n.to} to={n.to} end={n.to === '/'}
              className={({ isActive }) => `flex shrink-0 items-center gap-3 border-l-2 px-4 py-2 text-[13px] ${isActive
                ? 'border-s1 bg-rail-2 text-rail-ink' : 'border-transparent text-rail-ink-2 hover:bg-rail-2 hover:text-rail-ink'}`}>
              <span className="font-mono text-[10.5px] text-rail-ink-2">{n.code}</span>{n.label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto hidden border-t border-rail-line px-4 py-3 font-mono text-[10.5px] md:block">
          <div className="mb-1.5 uppercase tracking-[0.08em] text-rail-ink-2">System</div>
          <StatusLine k="Run" v={r ? `#${r.id} · ${r.finished_at ? new Date(r.finished_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'running'}` : '—'} ok={!!r} />
          <StatusLine k="Claims" v={r ? r.claims_analyzed.toLocaleString('en-IN') : '—'} ok={!!r} />
          <StatusLine k="Findings" v={r ? r.findings_count.toLocaleString('en-IN') : '—'} ok={!!r} />
          <StatusLine k="LLM" v={meta?.llm_enabled ? `${meta.llm_provider} · ${meta.llm_model}` : 'off · templates'} ok={meta?.llm_enabled} />
          <label className="mt-2 grid grid-cols-[64px_minmax(0,1fr)] items-center gap-2">
            <span className="text-rail-ink-2">Role</span>
            <select className="h-7 border border-rail-line bg-rail-2 px-1 text-rail-ink" value={role} onChange={e => changeRole(e.target.value)}>
              <option value="reviewer">Reviewer</option>
              <option value="viewer">Viewer · read-only</option>
            </select>
          </label>
        </div>
      </aside>
      <main className="min-w-0 px-4 py-5 md:px-7">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/alerts/:id" element={<AlertDetail role={role} />} />
          <Route path="/ingestion" element={<Ingestion />} />
          <Route path="/documents" element={<Documents />} />
          <Route path="/quality" element={<Quality />} />
        </Routes>
      </main>
    </div>
  )
}
