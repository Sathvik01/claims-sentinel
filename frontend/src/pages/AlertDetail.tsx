import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, inr, type ClaimBundle, type Explanation, type FindingDetail, type FindingRow } from '../api'
import { BasisBadge, Badge, Button, Card, SeverityBadge, State, StatusBadge, TypeBadge, inputCls } from '../components/ui'

const OUTCOME: Record<string, { icon: string; cls: string; label: string }> = {
  PASS: { icon: '✓', cls: 'text-good', label: 'Pass' },
  FAIL: { icon: '✕', cls: 'text-critical', label: 'Fail' },
  DEDUCTED: { icon: '↓', cls: 'text-warn', label: 'Deducted correctly' },
  NA: { icon: '–', cls: 'text-ink-3', label: 'Not applicable' },
}

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3 py-1 text-[12.5px]">
      <span className="text-ink-2">{k}</span><span className="tabular text-right font-medium">{v ?? '—'}</span>
    </div>
  )
}

function Evidence({ f }: { f: FindingRow }) {
  const ev = (f.evidence || {}) as Record<string, unknown>
  const parts = ev.score_parts as Record<string, number> | undefined
  const skip = new Set(['score_parts', 'reconciliation', 'clarification_request', 'missing_fields', 'impact', 'terms'])
  const rest = Object.entries(ev).filter(([k, v]) => !skip.has(k) && v !== null && typeof v !== 'object')
  return (
    <div className="space-y-3">
      {parts && (
        <div>
          <div className="mb-1.5 text-xs font-medium text-ink-2">Similarity breakdown (score {String(ev.score)})</div>
          {Object.entries(parts).filter(([k]) => !['days_apart', 'amount_diff_pct'].includes(k)).map(([k, v]) => (
            <div key={k} className="flex items-center gap-2 py-0.5 text-xs">
              <span className="w-36 text-ink-2">{k.replace(/_/g, ' ')}</span>
              <div className="h-1.5 flex-1 bg-surface"><div className="h-1.5 bg-s1" style={{ width: `${v * 100}%` }} /></div>
              <span className="tabular w-10 text-right">{v.toFixed(2)}</span>
            </div>
          ))}
          <div className="mt-1 text-xs text-ink-3">{parts.days_apart} day(s) apart · amount difference {parts.amount_diff_pct}%</div>
        </div>
      )}
      {!!ev.clarification_request && (
        <div className="border-l-2 border-accent bg-info-bg px-3 py-2 text-[12.5px]">
          <div className="mb-1 text-xs font-semibold text-accent">Clarification request</div>{String(ev.clarification_request)}
        </div>
      )}
      {rest.length > 0 && (
        <div className="grid gap-x-6 sm:grid-cols-2">
          {rest.map(([k, v]) => <KV key={k} k={k.replace(/_/g, ' ')} v={typeof v === 'number' ? v.toLocaleString('en-IN') : String(v)} />)}
        </div>
      )}
      {Array.isArray(ev.problems) && <ul className="list-disc pl-5 text-[12.5px]">{(ev.problems as string[]).map(p => <li key={p}>{p}</li>)}</ul>}
    </div>
  )
}

function Waterfall({ a, flaggedTitles }: { a: NonNullable<ClaimBundle['assessment']>; flaggedTitles: Set<string> }) {
  const max = Math.max(...a.waterfall.map(s => Math.abs(s.amount)), 1)
  return (
    <div>
      <div className="space-y-1">
        {a.waterfall.map((s, i) => {
          const total = s.kind === 'total' || s.kind === 'subtotal' || s.kind === 'proposed'
          const missed = !total && flaggedTitles.has(s.label)
          if (!total && Math.abs(s.amount) < 0.005) return null
          return (
            <div key={i} className={`grid grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_110px] items-center gap-3 border-b border-line px-2 py-1 text-[12.5px] ${missed ? 'bg-critical-bg' : ''}`}>
              <span className={`truncate ${total ? 'font-semibold' : 'text-ink-2'}`} title={s.label}>
                {s.rule_id && <span className="mr-1 font-mono text-[11px] text-ink-3">{s.rule_id}</span>}{s.label}
                {missed && <span className="ml-1 text-xs font-medium text-critical">· missed by handler</span>}
              </span>
              <div className="h-3 rounded-sm bg-surface">
                <div className="h-3 rounded-sm" style={{
                  width: `${Math.max(1, (Math.abs(s.amount) / max) * 100)}%`,
                  background: s.kind === 'proposed' ? 'var(--series-2)' : total ? 'var(--series-1)' : 'var(--ink-3)',
                }} />
              </div>
              <span className={`tabular text-right ${total ? 'font-semibold' : ''}`}>{inr(s.amount, 2)}</span>
            </div>
          )
        })}
      </div>
      {a.reconciliation && <p className="mt-3 border-l-2 border-ink bg-surface px-3 py-2 text-[12.5px] text-ink-2">{a.reconciliation}</p>}
    </div>
  )
}

function Lines({ b, highlight }: { b: ClaimBundle; highlight: Set<string> }) {
  const other = new Set(b.findings.flatMap(f => f.line_ids))
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-[12.5px]">
        <thead className="border-b border-line bg-surface text-left text-ink-2">
          <tr><th className="px-3 py-2">Line</th><th className="px-3 py-2">Date</th><th className="px-3 py-2">Service</th>
            <th className="px-3 py-2 text-right">Qty</th><th className="px-3 py-2 text-right">Rate</th><th className="px-3 py-2 text-right">Amount</th></tr>
        </thead>
        <tbody>
          {b.lines.map(l => (
            <tr key={l.line_id} className={`border-b border-line ${highlight.has(l.line_id) ? 'bg-critical-bg' : other.has(l.line_id) ? 'bg-warn-bg/60' : ''}`}>
              <td className="px-3 py-1.5 font-mono text-xs">{l.line_id.split('-').pop()}</td>
              <td className="tabular px-3 py-1.5 text-ink-2">{l.service_date}</td>
              <td className="px-3 py-1.5">{l.description}<div className="text-xs text-ink-3">{l.service_code} · {l.category}</div></td>
              <td className="tabular px-3 py-1.5 text-right">{l.quantity}</td>
              <td className="tabular px-3 py-1.5 text-right">{inr(l.unit_price)}</td>
              <td className="tabular px-3 py-1.5 text-right">{inr(l.amount)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Compare({ a, b }: { a: ClaimBundle; b: ClaimBundle }) {
  const rows: [string, (x: ClaimBundle) => React.ReactNode][] = [
    ['Claim', x => x.claim.claim_id], ['Status', x => x.claim.status], ['Invoice no.', x => x.claim.invoice_no],
    ['Provider', x => x.provider?.name], ['Incident', x => x.claim.incident_date], ['Admission → discharge', x => `${x.claim.admission_date ?? '—'} → ${x.claim.discharge_date ?? '—'}`],
    ['Diagnosis', x => x.claim.diagnosis_code], ['Requested', x => inr(x.claim.requested_amount)],
    ['Proposed / paid', x => x.claim.status === 'PAID' ? `paid ${inr(x.settlements[0]?.paid_amount)}` : inr(x.claim.proposed_settlement)],
    ['Submitted', x => x.claim.submitted_date], ['Lines', x => x.lines.length],
  ]
  return (
    <table className="w-full text-[12.5px]">
      <thead className="text-left text-xs text-ink-2"><tr><th className="py-1" /><th className="py-1">This claim</th><th className="py-1">Matched claim</th></tr></thead>
      <tbody>
        {rows.map(([k, fn]) => {
          const va = fn(a), vb = fn(b)
          const same = String(va) === String(vb)
          return (
            <tr key={k} className="border-b border-line">
              <td className="py-1.5 pr-2 text-ink-2">{k}</td>
              <td className={`tabular py-1.5 pr-2 ${same ? '' : 'font-semibold'}`}>{va}</td>
              <td className={`tabular py-1.5 ${same ? '' : 'font-semibold'}`}>{vb} {same && <span className="text-xs text-ink-3">=</span>}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

export default function AlertDetail({ role }: { role: string }) {
  const { id } = useParams()
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['finding', id], queryFn: () => api.get<FindingDetail>(`/api/findings/${id}`) })
  const [mode, setMode] = useState<'' | 'CORRECT' | 'DISMISS'>('')
  const [reason, setReason] = useState('')
  const [amount, setAmount] = useState('')
  const act = useMutation({
    mutationFn: (body: { action: string; reason?: string; amount?: number }) => api.post<FindingDetail>(`/api/findings/${id}/action`, body),
    onSuccess: d => { qc.setQueryData(['finding', id], d); qc.invalidateQueries({ queryKey: ['summary'] }); qc.invalidateQueries({ queryKey: ['findings'] }); setMode(''); setReason(''); setAmount('') },
  })
  const explain = useMutation({
    mutationFn: (refresh: boolean) => api.post<Explanation>(`/api/findings/${id}/explain?refresh=${refresh}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['finding', id] }),
  })

  return (
    <State loading={q.isLoading} error={q.error}>
      {q.data && (() => {
        const { finding: f, claim: b, related, actions } = q.data
        const expl = (explain.data || f.ai_explanation) as Explanation | null | undefined
        const flaggedTitles = new Set(b.findings.filter(x => x.finding_type === 'CONFIRMED_VIOLATION').map(x => x.title))
        const readOnly = role !== 'reviewer'
        return (
          <div className="space-y-6">
            <div className="grid gap-4 border-b-2 border-rule pb-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
              <div className="min-w-0">
                <div className="eyebrow">
                  <Link to="/alerts" className="text-accent hover:underline">02 — Alerts</Link> / Finding #{f.id} / {f.category_label}
                </div>
                <h1 className="mt-1 font-serif text-[28px] font-semibold leading-tight tracking-[-0.01em]">{f.title}</h1>
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <TypeBadge type={f.finding_type} /><SeverityBadge s={f.severity} /><StatusBadge s={f.status} />
                  <span className="num ml-2 text-[12px] text-ink-2">{f.claim_id}</span>
                  <span className="text-[12.5px] text-ink-2">· {b.claim.claimant_name} · {b.provider?.name}</span>
                  {f.related_claim_id && <span className="num text-[12px] text-ink-2">· ↔ {f.related_claim_id}</span>}
                </div>
              </div>
              <div className="grid grid-cols-2 gap-px border border-line bg-line">
                <div className="min-w-40 bg-panel px-3 py-2">
                  <div className="eyebrow">Billed excess</div>
                  <div className="num mt-1 text-[20px] font-medium leading-none">{inr(f.billed_excess, 2)}</div>
                </div>
                <div className="min-w-40 bg-panel px-3 py-2">
                  <div className="eyebrow flex items-center gap-1.5">
                    {f.amount_basis === 'ESTIMATED' ? 'Est. leakage' : f.amount_basis === 'CALCULATED' ? 'Overpayment' : 'Amount'} <BasisBadge basis={f.amount_basis} />
                  </div>
                  <div className="num mt-1 text-[20px] font-medium leading-none">{f.amount_basis === 'ESTIMATED' ? '≈ ' : ''}{inr(f.effective_amount, 2)}</div>
                  {f.status === 'CORRECTED' && <div className="num mt-1 text-[11px] text-ink-3">engine {inr(f.leakage_amount, 2)}</div>}
                </div>
              </div>
            </div>

            <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
              <div className="min-w-0 space-y-6">
                <Card code="1" title="Why it was flagged">
                  <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-ink-2">
                    <Badge tone="info">Rule {f.rule_id}</Badge>{f.clause_ref && <span>{f.clause_ref}</span>}
                    {f.finding_type === 'SUSPECTED_PATTERN' && <span>· confidence {(f.confidence * 100).toFixed(0)}%</span>}
                  </div>
                  <p className="mb-4 text-[12.5px] leading-relaxed">{f.calculation}</p>
                  <Evidence f={f} />
                </Card>

                <Card code="2" title="AI explanation" actions={
                  <Button variant={expl ? 'ghost' : 'primary'} disabled={explain.isPending} onClick={() => explain.mutate(!!expl)}>
                    {explain.isPending ? 'Generating…' : expl ? 'Regenerate' : 'Explain this finding'}
                  </Button>}>
                  {explain.error && <p className="text-[12.5px] text-critical">{(explain.error as Error).message}</p>}
                  {expl ? (
                    <div className="space-y-2 text-[12.5px]">
                      <p className="font-medium">{expl.summary}</p>
                      <p className="text-ink-2">{expl.why_flagged}</p>
                      <ul className="list-disc pl-5">{expl.what_to_check.map(x => <li key={x}>{x}</li>)}</ul>
                      {expl.clarification_request && <p className="border-l-2 border-accent bg-info-bg px-3 py-2">{expl.clarification_request}</p>}
                      <p className="text-xs text-ink-3">Source: {expl.source === 'llm' ? 'Groq LLM, checked against the evidence (no new figures allowed)' : 'deterministic template'}{expl.note ? ` — ${expl.note}` : ''}</p>
                    </div>
                  ) : <p className="text-[12.5px] text-ink-3">Generates a plain-language summary grounded only in the evidence above.</p>}
                </Card>

                {b.assessment && b.assessment.waterfall.length > 0 && (
                  <Card code="3" title="Settlement recalculation" actions={<span className="text-xs text-ink-3">billed → deductions → deductible → co-pay → cap</span>}>
                    <Waterfall a={b.assessment} flaggedTitles={flaggedTitles} />
                  </Card>
                )}

                {related && (
                  <Card code="4" title={`Side-by-side · ${related.claim.claim_id}`}><Compare a={b} b={related} /></Card>
                )}

                <Card code="5" flush title={`Invoice lines · ${b.claim.invoice_no ?? 'no invoice number'}`}
                  actions={<span className="text-xs text-ink-3"><span className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-critical-bg align-middle" />this finding <span className="ml-2 mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-warn-bg align-middle" />other findings</span>}>
                  <Lines b={b} highlight={new Set(f.line_ids)} />
                </Card>
                {related && related.lines.length > 0 && (
                  <Card code="6" flush title={`Matched claim lines · ${related.claim.claim_id}`}><Lines b={related} highlight={new Set((f.evidence?.matched_line ? [String(f.evidence.matched_line)] : []))} /></Card>
                )}
              </div>

              <div className="space-y-6">
                <Card code="R" title="Review decision">
                  {readOnly && <p className="mb-3 rounded-lg bg-surface p-2 text-xs text-ink-2">Viewer role is read-only. Switch to Reviewer to act.</p>}
                  <div className="flex flex-wrap gap-2">
                    <Button variant="primary" disabled={readOnly || act.isPending || f.status === 'CONFIRMED'} onClick={() => act.mutate({ action: 'CONFIRM' })}>Confirm</Button>
                    <Button disabled={readOnly || act.isPending} onClick={() => { setMode('CORRECT'); setAmount(String(f.effective_amount)) }}>Correct amount</Button>
                    <Button variant="danger" disabled={readOnly || act.isPending || f.status === 'DISMISSED'} onClick={() => setMode('DISMISS')}>Dismiss</Button>
                    {f.status !== 'OPEN' && <Button variant="ghost" disabled={readOnly || act.isPending} onClick={() => act.mutate({ action: 'REOPEN' })}>Reopen</Button>}
                  </div>
                  {mode && (
                    <form className="mt-3 space-y-2" onSubmit={e => { e.preventDefault(); act.mutate({ action: mode, reason, amount: mode === 'CORRECT' ? Number(amount) : undefined }) }}>
                      {mode === 'CORRECT' && (
                        <label className="block text-xs text-ink-2">Corrected leakage amount (₹)
                          <input className={`${inputCls} mt-1 w-full`} type="number" min={0} step="0.01" required value={amount} onChange={e => setAmount(e.target.value)} />
                        </label>
                      )}
                      <label className="block text-xs text-ink-2">Reason (required)
                        <textarea className={`${inputCls} mt-1 w-full`} rows={2} required value={reason} onChange={e => setReason(e.target.value)}
                          placeholder={mode === 'DISMISS' ? 'e.g. separate episode confirmed by hospital' : 'e.g. room category upgrade approved'} />
                      </label>
                      <div className="flex gap-2">
                        <Button type="submit" variant="primary" disabled={act.isPending}>Save {mode === 'CORRECT' ? 'correction' : 'dismissal'}</Button>
                        <Button variant="ghost" onClick={() => setMode('')}>Cancel</Button>
                      </div>
                    </form>
                  )}
                  {act.error && <p className="mt-2 text-[12.5px] text-critical">{(act.error as Error).message}</p>}
                  <div className="mt-4 border-b border-line pt-3">
                    <div className="mb-2 text-xs font-medium text-ink-2">Audit trail</div>
                    {actions.length === 0 ? <p className="text-xs text-ink-3">No decisions yet.</p> : (
                      <ol className="space-y-2">
                        {actions.map(a => (
                          <li key={a.id} className="text-xs">
                            <span className="font-medium">{a.action.toLowerCase()}</span> by {a.user} · {new Date(a.created_at).toLocaleString()}
                            {a.old_amount !== a.new_amount && <div className="tabular text-ink-2">{inr(a.old_amount, 2)} → {inr(a.new_amount, 2)}</div>}
                            {a.reason && <div className="text-ink-2">“{a.reason}”</div>}
                          </li>
                        ))}
                      </ol>
                    )}
                  </div>
                </Card>

                <Card code="C" title="Claim & policy">
                  <KV k="Type" v={b.claim.claim_type} />
                  <KV k="Diagnosis" v={b.claim.diagnosis_code} />
                  <KV k="Incident" v={b.claim.incident_date} />
                  <KV k="Stay" v={b.claim.admission_date ? `${b.claim.admission_date} → ${b.claim.discharge_date ?? '?'}` : '—'} />
                  <KV k="Requested" v={inr(b.claim.requested_amount, 2)} />
                  <KV k="Proposed settlement" v={inr(b.claim.proposed_settlement, 2)} />
                  <KV k="Expected (engine)" v={inr(b.assessment?.expected_settlement, 2)} />
                  {b.policy && <>
                    <div className="my-2 border-b border-line" />
                    <KV k="Policy" v={`${b.policy.policy_no} · ${b.policy.plan}`} />
                    <KV k="Status / period" v={`${b.policy.status} · ${b.policy.start_date} → ${b.policy.end_date}`} />
                    <KV k="Sum insured" v={inr(Number(b.policy.sum_insured))} />
                    <KV k="Deductible / co-pay" v={`${inr(Number(b.policy.deductible))} / ${b.policy.copay_pct}%`} />
                    <KV k="Room / ICU limit per day" v={`${inr(Number(b.policy.room_rent_limit))} / ${inr(Number(b.policy.icu_limit))}`} />
                  </>}
                </Card>

                {b.assessment && (
                  <Card code="V" title="Rules evaluated">
                    <ul className="space-y-1.5">
                      {b.assessment.checks.map(c => {
                        const o = OUTCOME[c.outcome] || OUTCOME.NA
                        return (
                          <li key={c.rule_id} className="text-xs" title={c.detail}>
                            <span className={`mr-1.5 inline-block w-3 font-bold ${o.cls}`} aria-label={o.label}>{o.icon}</span>
                            <span className="font-mono text-ink-3">{c.rule_id}</span> {c.name}
                            <div className="ml-5 text-ink-3">{o.label}: {c.detail}</div>
                          </li>
                        )
                      })}
                    </ul>
                    {b.assessment.cleared.length > 0 && (
                      <div className="mt-3 border-l-2 border-good bg-good-bg px-3 py-2 text-xs text-good">
                        <div className="mb-1 font-semibold">Considered and cleared</div>
                        {b.assessment.cleared.map((c, i) => <div key={i}>✓ {c.detail}</div>)}
                      </div>
                    )}
                  </Card>
                )}

                {b.findings.length > 1 && (
                  <Card code="O" title="Other findings on this claim">
                    <ul className="space-y-1.5 text-[12.5px]">
                      {b.findings.filter(x => x.id !== f.id).map(x => (
                        <li key={x.id}><Link className="text-accent" to={`/alerts/${x.id}`}>{x.title}</Link> <span className="tabular text-xs text-ink-3">{inr(x.leakage_amount)}</span></li>
                      ))}
                    </ul>
                  </Card>
                )}
              </div>
            </div>
          </div>
        )
      })()}
    </State>
  )
}
