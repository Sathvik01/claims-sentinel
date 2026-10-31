import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api, inr, inrShort, type ClaimRow, type ClaimsPage } from '../api'
import { useAuth } from '../auth'
import { LevelBadge } from '../components/ScanPanel'
import { Badge, Button, Card, FilterBar, Meta, PageHeader, SeverityBadge, State, type Tone, filterQuery } from '../components/ui'

export const REVIEW_STATUS: Record<string, { label: string; tone: Tone }> = {
  OPEN: { label: 'Open findings', tone: 'critical' },
  NEEDS_INFO: { label: 'Needs info', tone: 'info' },
  REVIEWED: { label: 'Reviewed', tone: 'warn' },
  CLEAN: { label: 'Clean', tone: 'good' },
  APPROVED: { label: 'Approved', tone: 'good' },
  ADJUSTED: { label: 'Adjusted', tone: 'warn' },
  ON_HOLD: { label: 'On hold', tone: 'serious' },
  REJECTED: { label: 'Rejected', tone: 'critical' },
  PAID: { label: 'Paid (history)', tone: 'neutral' },
}

export function ReviewStatusBadge({ s }: { s: string }) {
  const m = REVIEW_STATUS[s] ?? { label: s, tone: 'neutral' as Tone }
  return <Badge tone={m.tone}>{m.label}</Badge>
}

const COLS: { key: string; label: string; numeric?: boolean }[] = [
  { key: 'claim_id', label: 'Claim' }, { key: 'claimant_name', label: 'Claimant' }, { key: 'provider_name', label: 'Provider' },
  { key: 'incident_date', label: 'Incident' }, { key: 'requested_amount', label: 'Requested', numeric: true },
  { key: 'proposed_settlement', label: 'Proposed', numeric: true }, { key: 'expected_settlement', label: 'Expected', numeric: true },
  { key: 'leakage', label: 'Leakage', numeric: true }, { key: 'findings_open', label: 'Findings' },
  { key: 'max_severity', label: 'Sev' }, { key: 'state', label: 'Stage' }, { key: 'review_status', label: 'Status' },
]

function toCsv(rows: ClaimRow[]): string {
  const cols = ['claim_id', 'claimant_name', 'provider_name', 'claim_type', 'incident_date', 'source', 'requested_amount',
    'proposed_settlement', 'expected_settlement', 'leakage_calculated', 'leakage_estimated', 'findings_confirmed',
    'findings_suspected', 'findings_needs_info', 'max_severity', 'review_status'] as const
  const esc = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`
  return [cols.join(','), ...rows.map(r => cols.map(c => esc(r[c])).join(','))].join('\n')
}

const QUEUE_COPY: Record<string, { title: string; desc: string }> = {
  HANDLER: { title: 'Triage queue', desc: 'New submissions that passed analysis without findings. Fast-track clean claims within ₹50,000; route anything else to an analyst.' },
  ANALYST: { title: 'My investigations', desc: 'Flagged claims assigned to you. Decide every finding, accept or override corrections, and record the claim decision within your authority.' },
  SENIOR: { title: 'Escalations & approvals', desc: 'Analyst escalations, overrides beyond the threshold and decisions above analyst authority.' },
  SIU: { title: 'SIU referrals', desc: 'Claims referred for special investigation and claims on hold.' },
}

export default function Claims({ queue = false }: { queue?: boolean }) {
  const { me } = useAuth()
  const mine = useQuery({ queryKey: ['queue'], queryFn: () => api.get<{ role: string; claim_ids: string[] }>('/api/queues/mine'), enabled: queue })
  const [params, setParams] = useSearchParams()
  const nav = useNavigate()
  const sort = params.get('sort') || '-leakage'
  const status = params.get('review_status') || ''
  const qs = filterQuery(params)
  const ids = queue ? (mine.data?.claim_ids ?? []).join(',') : ''
  const q = useQuery({
    queryKey: ['claims', qs, sort, status, queue, ids],
    enabled: !queue || mine.isSuccess,
    queryFn: () => queue && !ids ? Promise.resolve({ total: 0, counts: {}, items: [] } as ClaimsPage)
      : api.get<ClaimsPage>(`/api/claims?${qs}&sort=${sort}&limit=5000${status ? `&review_status=${status}` : ''}${ids ? `&ids=${ids}` : ''}`),
  })
  const copy = queue ? QUEUE_COPY[me?.role ?? ''] : undefined
  const set = (k: string, v: string) => { const p = new URLSearchParams(params); if (v) p.set(k, v); else p.delete(k); setParams(p, { replace: true }) }
  const counts = q.data?.counts ?? {}
  const total = Object.values(counts).reduce((a, b) => a + b, 0)
  const decided = ['APPROVED', 'ADJUSTED', 'ON_HOLD', 'REJECTED'].reduce((a, k) => a + (counts[k] || 0), 0)
  const download = () => {
    const url = URL.createObjectURL(new Blob([toCsv(q.data?.items || [])], { type: 'text/csv' }))
    const a = document.createElement('a'); a.href = url; a.download = 'claims_register.csv'; a.click(); URL.revokeObjectURL(url)
  }
  const strip: [string, string, number][] = [
    ['', 'All pending', total], ['OPEN', 'Open findings', counts.OPEN || 0], ['NEEDS_INFO', 'Needs info', counts.NEEDS_INFO || 0],
    ['REVIEWED', 'Reviewed', counts.REVIEWED || 0], ['CLEAN', 'Clean', counts.CLEAN || 0], ['DECIDED', 'Decided', decided],
  ]

  return (
    <div className="space-y-6">
      <div>
        <PageHeader index={queue ? '02' : '03'} section={queue ? 'Work queue' : 'Submissions'} title={copy?.title ?? 'Claims register'}
          desc={copy?.desc ?? 'Every pending submission with its review status and lifecycle state. Clean claims are listed too, so nothing is reviewed only through its alerts.'}
          meta={<>
            <Meta label="Shown" value={q.data ? q.data.total.toLocaleString('en-IN') : '—'} />
            <Button onClick={download} disabled={!q.data?.items.length}>Export CSV</Button>
          </>} />
        <div className="grid grid-cols-3 gap-px border-b border-line bg-line md:grid-cols-6">
          {strip.map(([key, label, n]) => (
            <button key={label} onClick={() => set('review_status', key)}
              className={`px-3 py-2.5 text-left ${status === key ? 'bg-ink text-panel' : 'bg-panel hover:bg-surface'}`}>
              <div className={`eyebrow ${status === key ? '!text-panel/70' : ''}`}>{label}</div>
              <div className="num mt-0.5 text-[20px] font-medium leading-none">{n.toLocaleString('en-IN')}</div>
            </button>
          ))}
        </div>
        <FilterBar compact />
      </div>
      <Card code="C" title="Register" flush>
        <State loading={q.isLoading} error={q.error} empty={q.data?.items.length === 0 && 'No claims match.'}>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1150px] text-[12.5px]">
              <thead className="border-b border-line bg-surface text-left text-ink-2">
                <tr>{COLS.map(c => (
                  <th key={c.key} className={`cursor-pointer select-none px-3 py-1.5 ${c.numeric ? 'text-right' : ''}`}
                    onClick={() => set('sort', sort === `-${c.key}` ? c.key : `-${c.key}`)}>
                    {c.label}{sort.replace('-', '') === c.key ? (sort.startsWith('-') ? ' ↓' : ' ↑') : ''}
                  </th>))}</tr>
              </thead>
              <tbody>
                {q.data?.items.map(r => (
                  <tr key={r.claim_id} className="cursor-pointer border-b border-line hover:bg-surface" onClick={() => nav(`/investigations/${r.claim_id}`)}>
                    <td className="num px-3 py-1.5 text-[12px]">{r.claim_id}<div className="eyebrow">{r.claim_type.toLowerCase()} · {r.source}</div></td>
                    <td className="max-w-[160px] truncate px-3 py-1.5">{r.claimant_name}</td>
                    <td className="max-w-[180px] truncate px-3 py-1.5 text-ink-2">{r.provider_name}</td>
                    <td className="num px-3 py-1.5 text-ink-2">{r.incident_date}</td>
                    <td className="num px-3 py-1.5 text-right">{inr(r.requested_amount)}</td>
                    <td className="num px-3 py-1.5 text-right">{inr(r.proposed_settlement)}</td>
                    <td className="num px-3 py-1.5 text-right text-ink-2">{inr(r.expected_settlement)}</td>
                    <td className="num px-3 py-1.5 text-right">
                      {r.leakage_calculated ? inrShort(r.leakage_calculated) : ''}{r.leakage_estimated ? <span className="text-ink-3"> ≈{inrShort(r.leakage_estimated)}</span> : ''}{!r.leakage_calculated && !r.leakage_estimated ? '—' : ''}
                    </td>
                    <td className="num px-3 py-1.5 text-[11.5px]" title="confirmed ● · suspected ◆ · needs info ?">
                      {r.findings_confirmed ? `${r.findings_confirmed}● ` : ''}{r.findings_suspected ? `${r.findings_suspected}◆ ` : ''}{r.findings_needs_info ? `${r.findings_needs_info}?` : ''}
                      {!r.findings_confirmed && !r.findings_suspected && !r.findings_needs_info ? '—' : ''}
                    </td>
                    <td className="px-3 py-1.5">{r.max_severity ? <SeverityBadge s={r.max_severity} /> : ''}</td>
                    <td className="px-3 py-1.5 text-[11.5px] text-ink-2">{r.state.toLowerCase().replace(/_/g, ' ')}{r.assigned_to ? <div className="num text-ink-3">{r.assigned_to}</div> : null}</td>
                    <td className="px-3 py-1.5"><div className="flex items-center gap-1"><ReviewStatusBadge s={r.review_status} />{r.integrity?.level && <LevelBadge level={r.integrity.level} />}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </State>
      </Card>
      <p className="text-[11.5px] text-ink-3">Looking for a single issue instead? <Link className="text-accent" to="/alerts">Alerts</Link> lists findings one by one.</p>
    </div>
  )
}
