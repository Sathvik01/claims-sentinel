import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, login } from '../api'
import { Button, inputCls } from '../components/ui'

interface DemoAccounts { password: string; accounts: { username: string; full_name: string; role: string; role_label: string; policy_no: string | null }[] }

const RESPONSIBILITY: Record<string, string> = {
  POLICYHOLDER: 'File claims, upload evidence, answer requests, read decisions',
  HANDLER: 'Triage new submissions, fast-track clean claims up to ₹50,000',
  ANALYST: 'Investigate flagged claims, decide findings, approve up to ₹2,00,000',
  SENIOR: 'Resolve escalations, approve overrides and high-value claims',
  SIU: 'Investigate referrals: reused documents, tampering, related-party rings',
  ADMIN: 'Users, data ingestion, analysis runs, audit and quality',
}

export default function Login() {
  const demo = useQuery({ queryKey: ['demo-accounts'], queryFn: () => api.get<DemoAccounts>('/api/auth/demo-accounts'), retry: false })
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const signIn = async (u: string, p: string) => {
    setBusy(true); setError(null)
    try { await login(u, p) } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  return (
    <div className="grid min-h-screen lg:grid-cols-[420px_minmax(0,1fr)]">
      <aside className="flex flex-col justify-between bg-rail px-8 py-8 text-rail-ink">
        <div>
          <div className="flex items-center gap-2.5">
            <span className="grid h-9 w-9 place-items-center border border-rail-ink font-mono text-[13px] font-semibold">CS</span>
            <div className="font-serif text-[20px] font-semibold">Claims Sentinel</div>
          </div>
          <p className="mt-10 font-serif text-[30px] leading-tight">Every rupee of a claim, traced to a rule and a document.</p>
          <p className="mt-4 text-[13px] text-rail-ink-2">Validation, investigation and settlement correction for health insurance claims — with a full audit trail and a portal for policyholders.</p>
        </div>
        <p className="font-mono text-[10.5px] uppercase tracking-[0.08em] text-rail-ink-2">Level 2 · investigation workspace</p>
      </aside>
      <main className="px-6 py-8 lg:px-12">
        <div className="eyebrow">Sign in</div>
        <h1 className="mt-1 font-serif text-[26px] font-semibold">Access by role</h1>
        <form className="mt-5 grid max-w-xl gap-2 sm:grid-cols-[1fr_1fr_auto]" onSubmit={e => { e.preventDefault(); signIn(username, password) }}>
          <input className={inputCls} placeholder="Username" autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} />
          <input className={inputCls} placeholder="Password" type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} />
          <Button type="submit" variant="primary" disabled={busy || !username || !password}>Sign in</Button>
        </form>
        {error && <p className="mt-2 text-[12.5px] text-critical">{error}</p>}

        {demo.data && (
          <section className="mt-8 max-w-4xl border-t-2 border-rule">
            <div className="flex items-baseline justify-between border-b border-line py-2">
              <h2 className="font-mono text-[11px] font-medium uppercase tracking-[0.07em]">Demo accounts</h2>
              <span className="eyebrow">password <span className="text-ink">{demo.data.password}</span> · click to sign in</span>
            </div>
            <table className="w-full text-[12.5px]">
              <thead className="text-left text-ink-2"><tr className="border-b border-line"><th className="py-1.5 pr-3">Role</th><th className="py-1.5 pr-3">Account</th><th className="py-1.5">Responsibilities</th></tr></thead>
              <tbody>
                {demo.data.accounts.map(a => (
                  <tr key={a.username} className="cursor-pointer border-b border-line hover:bg-surface" onClick={() => signIn(a.username, demo.data!.password)}>
                    <td className="py-1.5 pr-3 font-medium">{a.role_label}</td>
                    <td className="py-1.5 pr-3"><span className="num text-[12px]">{a.username}</span> <span className="text-ink-3">· {a.full_name}{a.policy_no ? ` · ${a.policy_no}` : ''}</span></td>
                    <td className="py-1.5 text-ink-2">{RESPONSIBILITY[a.role]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-[11.5px] text-ink-3">Policyholder accounts appear after an admin loads the demo data.</p>
          </section>
        )}
      </main>
    </div>
  )
}
