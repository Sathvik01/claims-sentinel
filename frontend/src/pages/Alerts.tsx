import { useQuery } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, inr, type FindingRow } from '../api'
import { BasisBadge, Button, Card, FilterBar, Meta, PageHeader, SeverityBadge, State, StatusBadge, TypeBadge, filterQuery } from '../components/ui'

const COLS: { key: string; label: string; numeric?: boolean }[] = [
  { key: 'claim_id', label: 'Claim' },
  { key: 'title', label: 'Finding' },
  { key: 'finding_type', label: 'Type' },
  { key: 'severity', label: 'Severity' },
  { key: 'status', label: 'Status' },
  { key: 'billed_excess', label: 'Billed excess', numeric: true },
  { key: 'leakage_amount', label: 'Leakage', numeric: true },
  { key: 'provider_name', label: 'Provider' },
  { key: 'incident_date', label: 'Incident' },
]

function toCsv(rows: FindingRow[]): string {
  const cols = ['id', 'claim_id', 'category_label', 'rule_id', 'title', 'finding_type', 'amount_basis', 'severity', 'status',
    'billed_excess', 'leakage_amount', 'effective_amount', 'provider_name', 'claim_type', 'incident_date', 'calculation'] as const
  const esc = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`
  return [cols.join(','), ...rows.map(r => cols.map(c => esc(r[c])).join(','))].join('\n')
}

export default function Alerts() {
  const [params, setParams] = useSearchParams()
  const nav = useNavigate()
  const sort = params.get('sort') || '-leakage_amount'
  const qs = filterQuery(params)
  const q = useQuery({ queryKey: ['findings', qs, sort], queryFn: () => api.get<{ total: number; items: FindingRow[] }>(`/api/findings?${qs}&sort=${sort}&limit=5000`) })

  const setSort = (key: string) => {
    const p = new URLSearchParams(params)
    p.set('sort', sort === `-${key}` ? key : `-${key}`)
    setParams(p, { replace: true })
  }
  const download = () => {
    const url = URL.createObjectURL(new Blob([toCsv(q.data?.items || [])], { type: 'text/csv' }))
    const a = document.createElement('a')
    a.href = url
    a.download = 'leakage_alerts.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="space-y-4">
      <div>
        <PageHeader index="02" section="Review queue" title="Alerts"
          desc="Every finding with the rule or pattern behind it. Select a row to review the evidence and record a decision."
          meta={<>
            <Meta label="Findings" value={q.data ? q.data.total.toLocaleString('en-IN') : '—'} />
            <Meta label="Sort" value={sort.replace('-', '↓ ').replace(/_/g, ' ')} />
            <Button onClick={download} disabled={!q.data?.items.length}>Export CSV</Button>
          </>} />
        <FilterBar />
      </div>
      <Card code="R" title="Findings register" flush>
        <State loading={q.isLoading} error={q.error} empty={q.data?.items.length === 0 && 'No findings match the filters.'}>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1100px] text-[12.5px]">
              <thead className="border-b border-line bg-surface text-left text-ink-2">
                <tr>
                  {COLS.map(c => (
                    <th key={c.key} className={`cursor-pointer select-none px-3 py-1.5 font-medium ${c.numeric ? 'text-right' : ''}`} onClick={() => setSort(c.key)}>
                      {c.label}{sort.replace('-', '') === c.key ? (sort.startsWith('-') ? ' ↓' : ' ↑') : ''}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {q.data?.items.map(f => (
                  <tr key={f.id} onClick={() => nav(`/alerts/${f.id}`)} className="cursor-pointer border-b border-line hover:bg-surface">
                    <td className="num px-3 py-1.5 text-[12px]">{f.claim_id}<div className="eyebrow">{f.claim_type?.toLowerCase()}</div></td>
                    <td className="max-w-[340px] px-3 py-1.5">
                      <div className="truncate font-medium" title={f.title}>{f.title}</div>
                      <div className="text-xs text-ink-3">{f.category_label} · {f.rule_id}</div>
                    </td>
                    <td className="px-3 py-1.5"><TypeBadge type={f.finding_type} /></td>
                    <td className="px-3 py-1.5"><SeverityBadge s={f.severity} /></td>
                    <td className="px-3 py-1.5"><StatusBadge s={f.status} /></td>
                    <td className="tabular px-3 py-1.5 text-right text-ink-2">{inr(f.billed_excess)}</td>
                    <td className="tabular px-3 py-1.5 text-right">
                      <div className="font-semibold">{f.amount_basis === 'ESTIMATED' ? '≈ ' : ''}{inr(f.effective_amount)}</div>
                      <BasisBadge basis={f.amount_basis} />
                    </td>
                    <td className="max-w-[200px] truncate px-3 py-1.5 text-ink-2" title={f.provider_name}>{f.provider_name}</td>
                    <td className="tabular px-3 py-1.5 text-ink-2">{f.incident_date}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </State>
      </Card>
    </div>
  )
}
