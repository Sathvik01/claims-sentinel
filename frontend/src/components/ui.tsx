import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, type Meta } from '../api'

/** Section block: heavy top rule, mono caption, crisp divider under the header. */
export function Card({ title, code, actions, children, className = '', flush }: {
  title?: ReactNode; code?: string; actions?: ReactNode; children: ReactNode; className?: string; flush?: boolean
}) {
  return (
    <section className={`border-t-2 border-rule bg-panel ${className}`}>
      {(title || actions) && (
        <header className="flex min-h-9 items-center justify-between gap-3 border-b border-line px-3">
          <h2 className="flex items-baseline gap-2 font-mono text-[11px] font-medium uppercase tracking-[0.07em] text-ink">
            {code && <span className="text-ink-3">{code}</span>}{title}
          </h2>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      <div className={flush ? '' : 'p-3'}>{children}</div>
    </section>
  )
}

/** Left-aligned page head: index eyebrow, serif title, description; metadata pinned right. */
export function PageHeader({ index, section, title, desc, meta }: { index: string; section: string; title: string; desc?: ReactNode; meta?: ReactNode }) {
  return (
    <div className="grid gap-4 border-b-2 border-rule pb-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
      <div>
        <div className="eyebrow">{index} — {section}</div>
        <h1 className="mt-1 font-serif text-[28px] font-semibold leading-tight tracking-[-0.01em]">{title}</h1>
        {desc && <p className="mt-1 max-w-3xl text-[13px] text-ink-2">{desc}</p>}
      </div>
      {meta && <div className="flex flex-wrap items-end gap-x-6 gap-y-2 lg:justify-end">{meta}</div>}
    </div>
  )
}

export function Meta({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="border-l border-line pl-3">
      <div className="eyebrow">{label}</div>
      <div className="num text-[13px] text-ink">{value}</div>
    </div>
  )
}

const TONES = {
  neutral: 'text-ink-2 border-line bg-panel',
  info: 'text-accent border-accent/40 bg-info-bg',
  good: 'text-good border-good/40 bg-good-bg',
  warn: 'text-warn border-warn/40 bg-warn-bg',
  serious: 'text-serious border-serious/40 bg-serious-bg',
  critical: 'text-critical border-critical/40 bg-critical-bg',
}
export type Tone = keyof typeof TONES

export function Badge({ tone = 'neutral', children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return <span title={title} className={`inline-flex h-[18px] items-center gap-1 whitespace-nowrap border px-1.5 font-mono text-[10px] font-medium uppercase tracking-[0.04em] ${TONES[tone]}`}>{children}</span>
}

export function TypeBadge({ type }: { type: string }) {
  if (type === 'CONFIRMED_VIOLATION') return <Badge tone="critical" title="Deterministic policy rule violation">● Confirmed</Badge>
  if (type === 'SUSPECTED_PATTERN') return <Badge tone="warn" title="Pattern requiring human review">◆ Suspected</Badge>
  return <Badge tone="info" title="Cannot be assessed without more information">? Needs info</Badge>
}

export function BasisBadge({ basis }: { basis: string }) {
  if (basis === 'CALCULATED') return <Badge title="Exact amount from policy arithmetic">= Calc</Badge>
  if (basis === 'ESTIMATED') return <Badge title="Estimate for review — not a determined amount">≈ Est</Badge>
  return <Badge>n/d</Badge>
}

export function SeverityBadge({ s }: { s: string }) {
  const tone: Tone = s === 'HIGH' ? 'critical' : s === 'MEDIUM' ? 'serious' : 'neutral'
  const icon = s === 'HIGH' ? '▲' : s === 'MEDIUM' ? '■' : '▽'
  return <Badge tone={tone}>{icon} {s === 'MEDIUM' ? 'Med' : s[0] + s.slice(1).toLowerCase()}</Badge>
}

export function StatusBadge({ s }: { s: string }) {
  const tone: Tone = s === 'CONFIRMED' ? 'critical' : s === 'CORRECTED' ? 'warn' : s === 'DISMISSED' ? 'good' : 'neutral'
  return <Badge tone={tone}>{s[0] + s.slice(1).toLowerCase()}</Badge>
}

/** Figure cell, meant for ruled strips (`grid gap-px bg-line`). */
export function Kpi({ label, value, sub, accent }: { label: string; value: ReactNode; sub?: ReactNode; accent?: string }) {
  return (
    <div className="bg-panel px-3 py-2.5">
      <div className="eyebrow flex items-center gap-1.5">
        {accent && <span className="inline-block h-2 w-2" style={{ background: accent }} />}{label}
      </div>
      <div className="num mt-1 text-[20px] font-medium leading-none text-ink">{value}</div>
      {sub && <div className="mt-1 text-[11.5px] text-ink-3">{sub}</div>}
    </div>
  )
}

/** Statement-style row: label left, figure right-aligned in mono. */
export function LedgerRow({ label, value, sub, accent, to, strong }: {
  label: ReactNode; value: ReactNode; sub?: ReactNode; accent?: string; to?: string; strong?: boolean
}) {
  const body = (
    <div className={`grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 border-b border-line px-3 py-2 ${to ? 'hover:bg-surface' : ''}`}>
      <div className="flex items-center gap-1.5 text-[12.5px] text-ink-2">
        {accent && <span className="inline-block h-2 w-2 shrink-0" style={{ background: accent }} />}{label}
      </div>
      <div className={`num text-right text-ink ${strong ? 'text-[19px] font-medium' : 'text-[13px]'}`}>{value}</div>
      {sub && <div className="col-span-2 text-[11.5px] text-ink-3">{sub}</div>}
    </div>
  )
  return to ? <Link to={to} className="block">{body}</Link> : body
}

export function Button({ children, onClick, variant = 'secondary', disabled, type = 'button', title }: {
  children: ReactNode; onClick?: () => void; variant?: 'primary' | 'secondary' | 'danger' | 'ghost'; disabled?: boolean; type?: 'button' | 'submit'; title?: string
}) {
  const cls = {
    primary: 'bg-ink text-panel border-ink hover:bg-ink-2',
    secondary: 'bg-panel text-ink border-ink/70 hover:bg-surface',
    danger: 'bg-panel text-critical border-critical/60 hover:bg-critical-bg',
    ghost: 'bg-transparent text-ink-2 border-transparent hover:text-ink hover:underline',
  }[variant]
  return (
    <button type={type} title={title} disabled={disabled} onClick={onClick}
      className={`inline-flex h-8 items-center justify-center gap-1.5 border px-3 text-[12.5px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${cls}`}>
      {children}
    </button>
  )
}

export const inputCls = 'h-8 border border-line bg-panel px-2 text-[12.5px] text-ink outline-none focus:border-ink'

export function useMeta() {
  return useQuery({ queryKey: ['meta'], queryFn: () => api.get<Meta>('/api/meta') })
}

export const FILTER_KEYS = ['claim_type', 'provider_id', 'category', 'severity', 'status', 'finding_type', 'min_amount', 'max_amount', 'date_from', 'date_to', 'q'] as const

export function filterQuery(params: URLSearchParams): string {
  const q = new URLSearchParams()
  for (const k of FILTER_KEYS) { const v = params.get(k); if (v) q.set(k, v) }
  return q.toString()
}

const label = (s: string) => s.replace(/_/g, ' ').toLowerCase().replace(/^\w/, c => c.toUpperCase())

/** One-row filter toolbar between hard rules. */
export function FilterBar({ compact }: { compact?: boolean }) {
  const [params, setParams] = useSearchParams()
  const { data: meta } = useMeta()
  const set = (k: string, v: string) => {
    const p = new URLSearchParams(params)
    if (v) p.set(k, v); else p.delete(k)
    setParams(p, { replace: true })
  }
  const sel = (k: string, placeholder: string, opts: { value: string; label: string }[]) => (
    <select aria-label={placeholder} className={`${inputCls} ${params.get(k) ? 'border-ink font-medium' : ''}`} value={params.get(k) || ''} onChange={e => set(k, e.target.value)}>
      <option value="">{placeholder}</option>
      {opts.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  )
  const active = FILTER_KEYS.filter(k => params.get(k)).length
  return (
    <div className="flex flex-wrap items-center gap-1.5 border-b border-line bg-panel px-3 py-2">
      <span className="eyebrow mr-2 w-[72px] whitespace-nowrap">Filter{active ? ` · ${active}` : ''}</span>
      {sel('claim_type', 'Claim type', (meta?.claim_types || []).map(v => ({ value: v, label: label(v) })))}
      {sel('provider_id', 'Provider', (meta?.providers || []).map(p => ({ value: p.provider_id, label: p.name })))}
      {sel('category', 'Category', meta?.categories || [])}
      {sel('severity', 'Severity', (meta?.severities || []).map(v => ({ value: v, label: label(v) })))}
      {!compact && sel('finding_type', 'Finding type', (meta?.finding_types || []).map(v => ({ value: v, label: label(v) })))}
      {!compact && sel('status', 'Status', (meta?.statuses || []).map(v => ({ value: v, label: label(v) })))}
      <span className="mx-1 h-5 w-px bg-line" />
      <input aria-label="Minimum amount" className={`${inputCls} num w-24`} type="number" min={0} placeholder="₹ min" value={params.get('min_amount') || ''} onChange={e => set('min_amount', e.target.value)} />
      <input aria-label="Maximum amount" className={`${inputCls} num w-24`} type="number" min={0} placeholder="₹ max" value={params.get('max_amount') || ''} onChange={e => set('max_amount', e.target.value)} />
      <span className="mx-1 h-5 w-px bg-line" />
      <input aria-label="Incident from" className={`${inputCls} num`} type="date" value={params.get('date_from') || ''} onChange={e => set('date_from', e.target.value)} />
      <span className="text-ink-3">→</span>
      <input aria-label="Incident to" className={`${inputCls} num`} type="date" value={params.get('date_to') || ''} onChange={e => set('date_to', e.target.value)} />
      {!compact && <input aria-label="Search claim" className={`${inputCls} num w-32`} placeholder="CLM-…" value={params.get('q') || ''} onChange={e => set('q', e.target.value)} />}
      {active > 0 && <Button variant="ghost" onClick={() => setParams({}, { replace: true })}>Clear</Button>}
    </div>
  )
}

export function State({ loading, error, empty, children }: { loading?: boolean; error?: unknown; empty?: boolean | string; children?: ReactNode }) {
  if (loading) return <div className="eyebrow px-3 py-8">Loading…</div>
  if (error) return <div className="border-l-2 border-critical bg-critical-bg px-3 py-2 text-[12.5px] text-critical">{String((error as Error).message || error)}</div>
  if (empty) return <div className="px-3 py-8 text-[12.5px] text-ink-3">{typeof empty === 'string' ? empty : 'Nothing to show.'}</div>
  return <>{children}</>
}
