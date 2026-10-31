import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { api } from './api'
import { AuthProvider, useAuth } from './auth'
import { useMeta } from './components/ui'
import AlertDetail from './pages/AlertDetail'
import Alerts from './pages/Alerts'
import Claims from './pages/Claims'
import Dashboard from './pages/Dashboard'
import Documents from './pages/Documents'
import Ingestion from './pages/Ingestion'
import Investigation from './pages/Investigation'
import Login from './pages/Login'
import Quality from './pages/Quality'
import Portal from './portal/Portal'

const NAV = [
  { to: '/overview', code: '01', label: 'Overview', perm: 'findings:view' },
  { to: '/queue', code: '02', label: 'My queue', perm: 'claims:view', notFor: 'ADMIN' },
  { to: '/claims', code: '03', label: 'Claims register', perm: 'claims:view' },
  { to: '/alerts', code: '04', label: 'Alerts', perm: 'findings:view' },
  { to: '/ingestion', code: '05', label: 'Data ingestion', perm: 'data:ingest' },
  { to: '/documents', code: '06', label: 'Documents', perm: 'documents:view' },
  { to: '/quality', code: '07', label: 'Detection quality', perm: 'quality:view' },
]

interface Run { id: number; finished_at: string | null; claims_analyzed: number; findings_count: number }
interface Notifications { unread: number; items: { id: number; message: string; claim_id: string | null; created_at: string; read: boolean }[] }

function StatusLine({ k, v, ok }: { k: string; v: React.ReactNode; ok?: boolean }) {
  return (
    <div className="grid grid-cols-[64px_minmax(0,1fr)] gap-2 py-0.5">
      <span className="text-rail-ink-2">{k}</span>
      <span className={`truncate ${ok === false ? 'text-rail-ink-2' : 'text-rail-ink'}`}>{v}</span>
    </div>
  )
}

function StaffShell() {
  const { me, can, signOut } = useAuth()
  const qc = useQueryClient()
  const { data: meta } = useMeta()
  const run = useQuery({ queryKey: ['run'], queryFn: () => api.get<Run | null>('/api/analysis/latest'), refetchInterval: 15000 })
  const notes = useQuery({ queryKey: ['notifications'], queryFn: () => api.get<Notifications>('/api/notifications'), refetchInterval: 20000 })
  const markRead = useMutation({ mutationFn: () => api.post('/api/notifications/read'), onSuccess: () => qc.invalidateQueries({ queryKey: ['notifications'] }) })
  const r = run.data
  const nav = NAV.filter(n => can(n.perm) && n.notFor !== me?.role)
  return (
    <div className="min-h-screen md:grid md:grid-cols-[228px_minmax(0,1fr)]">
      <aside className="flex flex-col bg-rail text-rail-ink md:sticky md:top-0 md:h-screen">
        <div className="border-b border-rail-line px-4 py-4">
          <div className="flex items-center gap-2.5">
            <span className="grid h-8 w-8 place-items-center border border-rail-ink font-mono text-[12px] font-semibold">CS</span>
            <div>
              <div className="font-serif text-[16px] font-semibold leading-none">Claims Sentinel</div>
              <div className="mt-1 font-mono text-[10px] uppercase tracking-[0.08em] text-rail-ink-2">Health claims · L2</div>
            </div>
          </div>
        </div>
        <nav className="flex gap-0 overflow-x-auto md:block md:py-2">
          {nav.map(n => (
            <NavLink key={n.to} to={n.to} end={n.to === '/'}
              className={({ isActive }) => `flex shrink-0 items-center gap-3 border-l-2 px-4 py-2 text-[13px] ${isActive
                ? 'border-s1 bg-rail-2 text-rail-ink' : 'border-transparent text-rail-ink-2 hover:bg-rail-2 hover:text-rail-ink'}`}>
              <span className="font-mono text-[10.5px] text-rail-ink-2">{n.code}</span>{n.label}
            </NavLink>
          ))}
        </nav>
        {notes.data && notes.data.items.length > 0 && (
          <div className="hidden border-t border-rail-line px-4 py-3 md:block">
            <div className="mb-1.5 flex items-center justify-between font-mono text-[10.5px] uppercase tracking-[0.08em] text-rail-ink-2">
              <span>Inbox{notes.data.unread ? <span className="ml-1 text-rail-ink">· {notes.data.unread} new</span> : ''}</span>
              {notes.data.unread > 0 && <button className="normal-case hover:text-rail-ink" onClick={() => markRead.mutate()}>mark read</button>}
            </div>
            <ul className="max-h-36 space-y-1 overflow-y-auto text-[11.5px]">
              {notes.data.items.slice(0, 6).map(n => (
                <li key={n.id} className={n.read ? 'text-rail-ink-2' : 'text-rail-ink'}>{n.read ? '' : '● '}{n.message}</li>
              ))}
            </ul>
          </div>
        )}
        <div className="mt-auto hidden border-t border-rail-line px-4 py-3 font-mono text-[10.5px] md:block">
          <div className="mb-1.5 uppercase tracking-[0.08em] text-rail-ink-2">System</div>
          <StatusLine k="Run" v={r ? `#${r.id} · ${r.finished_at ? new Date(r.finished_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'running'}` : '—'} ok={!!r} />
          <StatusLine k="Claims" v={r ? r.claims_analyzed.toLocaleString('en-IN') : '—'} ok={!!r} />
          <StatusLine k="LLM" v={meta?.llm_enabled ? `${meta.llm_provider} · ${meta.llm_model}` : 'off · templates'} ok={meta?.llm_enabled} />
        </div>
        <div className="border-t border-rail-line px-4 py-3">
          <div className="text-[13px] text-rail-ink">{me?.full_name}</div>
          <div className="mt-0.5 flex items-center justify-between font-mono text-[10.5px] uppercase tracking-[0.06em] text-rail-ink-2">
            <span>{me?.role_label}{me?.authority_limit ? ` · ≤ ₹${me.authority_limit.toLocaleString('en-IN')}` : ''}</span>
            <button className="hover:text-rail-ink" onClick={signOut}>Sign out</button>
          </div>
        </div>
      </aside>
      <main className="min-w-0 px-4 py-5 md:px-7">
        <Routes>
          <Route path="/" element={me?.role === 'ADMIN' || me?.role === 'SENIOR' ? <Dashboard /> : <Navigate to="/queue" replace />} />
          <Route path="/overview" element={<Dashboard />} />
          <Route path="/queue" element={<Claims queue key="queue" />} />
          <Route path="/claims" element={<Claims key="register" />} />
          <Route path="/investigations/:id" element={<Investigation />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/alerts/:id" element={<AlertDetail />} />
          <Route path="/ingestion" element={<Ingestion />} />
          <Route path="/documents" element={<Documents />} />
          <Route path="/quality" element={<Quality />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  )
}

function Gate() {
  const { me, loading } = useAuth()
  if (loading) return <div className="eyebrow p-8">Signing in…</div>
  if (!me) return <Login />
  if (me.role === 'POLICYHOLDER') return <Portal />
  return <StaffShell />
}

export default function App() {
  return <AuthProvider><Gate /></AuthProvider>
}
