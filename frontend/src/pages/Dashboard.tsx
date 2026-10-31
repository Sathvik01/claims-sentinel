import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, inr, inrShort, type FindingRow, type Summary } from '../api'
import { Button, Card, FilterBar, LedgerRow, Meta, PageHeader, SeverityBadge, State, TypeBadge, filterQuery } from '../components/ui'

const axisTick = { fill: 'var(--ink-2)', fontSize: 11, fontFamily: 'var(--font-mono)' }
const catTick = { fill: 'var(--ink-2)', fontSize: 12, fontFamily: 'var(--font-sans)' }

function MoneyTooltip({ active, payload, label }: { active?: boolean; payload?: { name: string; value: number; color: string; payload: { count: number } }[]; label?: string }) {
  if (!active || !payload?.length) return null
  return (
    <div className="border border-rule bg-panel px-2.5 py-2 text-[12px]">
      <div className="mb-1 font-medium text-ink">{label}</div>
      {payload.map(p => (
        <div key={p.name} className="flex items-center gap-2 text-ink-2">
          <span className="h-2 w-2" style={{ background: p.color }} />{p.name}<b className="num ml-auto pl-3 font-normal text-ink">{inr(p.value)}</b>
        </div>
      ))}
      <div className="eyebrow mt-1">{payload[0].payload.count} finding(s)</div>
    </div>
  )
}

function StackedBars({ data, labelKey, height, onSelect }: { data: object[]; labelKey: string; height: number; onSelect?: (row: Record<string, string>) => void }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout="vertical" margin={{ left: 0, right: 12, top: 4, bottom: 0 }} barCategoryGap={5}>
        <CartesianGrid horizontal={false} stroke="var(--line)" />
        <XAxis type="number" tick={axisTick} tickFormatter={v => inrShort(v)} stroke="var(--line)" />
        <YAxis type="category" dataKey={labelKey} tick={catTick} width={180} stroke="var(--rule)" />
        <Tooltip content={<MoneyTooltip />} cursor={{ fill: 'var(--sunken)' }} />
        <Legend align="left" verticalAlign="top" height={26} iconType="square" iconSize={9}
          wrapperStyle={{ fontSize: 11, fontFamily: 'var(--font-mono)', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--ink-2)' }} />
        <Bar dataKey="calculated" name="Calculated" stackId="a" fill="var(--series-1)" stroke="var(--panel)" strokeWidth={2}
          onClick={d => onSelect?.(d.payload as Record<string, string>)} style={{ cursor: onSelect ? 'pointer' : undefined }} />
        <Bar dataKey="estimated" name="Estimated" stackId="a" fill="var(--series-2)" stroke="var(--panel)" strokeWidth={2} radius={[0, 4, 4, 0]}
          onClick={d => onSelect?.(d.payload as Record<string, string>)} style={{ cursor: onSelect ? 'pointer' : undefined }} />
      </BarChart>
    </ResponsiveContainer>
  )
}

export default function Dashboard() {
  const [params] = useSearchParams()
  const qs = filterQuery(params)
  const nav = useNavigate()
  const qc = useQueryClient()
  const summary = useQuery({ queryKey: ['summary', qs], queryFn: () => api.get<Summary>(`/api/dashboard/summary?${qs}`) })
  const top = useQuery({
    queryKey: ['findings', 'top', qs],
    queryFn: () => api.get<{ items: FindingRow[] }>(`/api/findings?${qs}&sort=-leakage_amount&limit=12`),
  })
  const load = useMutation({ mutationFn: () => api.post('/api/demo/load-sample'), onSuccess: () => qc.invalidateQueries() })
  const s = summary.data
  const withFilter = (k: string, v: string) => { const p = new URLSearchParams(params); p.set(k, v); return `/alerts?${p}` }
  const total = s ? s.calculated_leakage + s.estimated_leakage : 0

  return (
    <div className="space-y-0">
      <PageHeader index="01" section="Portfolio exposure" title="Leakage overview"
        desc="Pending claims validated against policy terms, claim history and peer billing. Calculated amounts are exact policy arithmetic; estimated amounts are suspected patterns awaiting review."
        meta={s && s.claims_processed > 0 && <>
          <Meta label="Pending claims" value={s.claims_processed.toLocaleString('en-IN')} />
          <Meta label="Billed" value={inrShort(s.billed_amount)} />
          <Meta label="Proposed" value={inrShort(s.proposed_amount)} />
        </>} />
      <FilterBar compact />
      <State loading={summary.isLoading} error={summary.error}>
        {s && s.claims_processed === 0 ? (
          <div className="grid gap-px border-b border-line bg-line lg:grid-cols-[320px_1fr]">
            <div className="bg-panel p-4">
              <div className="eyebrow">Status</div>
              <div className="mt-1 font-serif text-[20px]">No claims loaded</div>
            </div>
            <div className="bg-panel p-4">
              <p className="max-w-xl text-[13px] text-ink-2">Load the synthetic health-insurance book (≈1,000 pending claims, 17 planted leakage scenarios, ~300 legitimate look-alikes) or upload your own CSV / Excel / JSON files.</p>
              <div className="mt-3 flex gap-2">
                <Button variant="primary" disabled={load.isPending} onClick={() => load.mutate()}>{load.isPending ? 'Loading & analysing…' : 'Load demo data'}</Button>
                <Link to="/ingestion"><Button>Upload files</Button></Link>
              </div>
            </div>
          </div>
        ) : s && (
          <div className="grid border-b border-line xl:grid-cols-[320px_minmax(0,1fr)]">
            {/* Ledger column */}
            <div className="border-line bg-panel xl:border-r">
              <div className="border-b border-rule px-3 pb-1.5 pt-3"><span className="eyebrow">A — Statement</span></div>
              <LedgerRow strong label="Total flagged exposure" value={inrShort(total)} sub="calculated + estimated" />
              <LedgerRow label="Calculated leakage" accent="var(--series-1)" value={inr(s.calculated_leakage)} sub="exact, from policy rules" />
              <LedgerRow label="Estimated leakage" accent="var(--series-2)" value={inr(s.estimated_leakage)} sub="suspected — needs review" />
              <LedgerRow label="Claims flagged" value={`${s.claims_flagged.toLocaleString('en-IN')} / ${s.claims_processed.toLocaleString('en-IN')}`} sub={`${s.flagged_pct}% of processed`} />
              <LedgerRow label="Leakage / proposed" value={s.proposed_amount ? `${(100 * s.calculated_leakage / s.proposed_amount).toFixed(2)}%` : '—'} />
              <div className="border-b border-rule px-3 pb-1.5 pt-4"><span className="eyebrow">B — Severity</span></div>
              {(['HIGH', 'MEDIUM', 'LOW'] as const).map(k => (
                <LedgerRow key={k} to={withFilter('severity', k)} label={<SeverityBadge s={k} />} value={s.by_severity[k] || 0} />
              ))}
              <div className="border-b border-rule px-3 pb-1.5 pt-4"><span className="eyebrow">C — Finding type</span></div>
              {(['CONFIRMED_VIOLATION', 'SUSPECTED_PATTERN', 'NEEDS_INFO'] as const).map(k => (
                <LedgerRow key={k} to={withFilter('finding_type', k)} label={<TypeBadge type={k} />} value={s.by_finding_type[k] || 0} />
              ))}
              <div className="border-b border-rule px-3 pb-1.5 pt-4"><span className="eyebrow">D — Review queue</span></div>
              {['OPEN', 'CONFIRMED', 'CORRECTED', 'DISMISSED'].map(k => (
                <LedgerRow key={k} to={withFilter('status', k)} label={k[0] + k.slice(1).toLowerCase()} value={s.by_status[k] || 0} />
              ))}
            </div>

            {/* Analysis column */}
            <div className="min-w-0">
              <Card code="E" title="Leakage by category"
                actions={<span className="eyebrow">select a bar → alerts</span>}>
                <StackedBars data={s.by_category} labelKey="label" height={Math.max(220, s.by_category.length * 28 + 50)}
                  onSelect={row => nav(withFilter('category', row.category))} />
              </Card>
              <div className="grid 2xl:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
                <Card code="F" title="Providers by flagged amount" className="2xl:border-r 2xl:border-r-line">
                  <StackedBars data={s.top_providers} labelKey="name" height={Math.max(200, s.top_providers.length * 28 + 50)}
                    onSelect={row => nav(withFilter('provider_id', row.provider_id))} />
                </Card>
                <Card code="G" title="Largest exposures" flush actions={<Link className="font-mono text-[11px] uppercase text-accent hover:underline" to={`/alerts?${qs}`}>All alerts →</Link>}>
                  <State loading={top.isLoading} error={top.error} empty={top.data?.items.length === 0 && 'No findings match the filters.'}>
                    <table className="w-full text-[12.5px]">
                      <thead className="text-left text-ink-3">
                        <tr className="border-b border-line"><th className="px-3 py-1.5">Finding</th><th className="px-2 py-1.5">Sev</th><th className="px-3 py-1.5 text-right">Amount</th></tr>
                      </thead>
                      <tbody>
                        {top.data?.items.map(f => (
                          <tr key={f.id} className="cursor-pointer border-b border-line last:border-0 hover:bg-surface" onClick={() => nav(`/alerts/${f.id}`)}>
                            <td className="max-w-0 px-3 py-1.5">
                              <div className="truncate text-ink">{f.title}</div>
                              <div className="num truncate text-[11px] text-ink-3">{f.claim_id} · {f.category_label}</div>
                            </td>
                            <td className="px-2 py-1.5"><SeverityBadge s={f.severity} /></td>
                            <td className="num whitespace-nowrap px-3 py-1.5 text-right">{f.amount_basis === 'ESTIMATED' ? '≈ ' : ''}{inr(f.effective_amount)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </State>
                </Card>
              </div>
            </div>
          </div>
        )}
      </State>
    </div>
  )
}
