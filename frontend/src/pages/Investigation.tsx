import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, authUrl, inr, type Workspace, type WsFinding } from '../api'
import { useAuth } from '../auth'
import { Badge, BasisBadge, Button, Card, Meta, SeverityBadge, State, StatusBadge, TypeBadge, inputCls, type Tone } from '../components/ui'
import { Waterfall } from './AlertDetail'

const TABS = ['Findings & corrections', 'Evidence', 'Related claims', 'Policy & rules', 'Notes', 'Timeline'] as const
const OPEN = new Set(['OPEN', 'EVIDENCE_REQUESTED', 'ESCALATED', 'PENDING_APPROVAL'])
const STATE_TONE: Record<string, Tone> = {
  INVESTIGATION: 'info', TRIAGE: 'info', INFO_REQUESTED: 'warn', ESCALATED_SENIOR: 'serious', ESCALATED_SIU: 'critical',
  PENDING_APPROVAL: 'warn', ON_HOLD: 'critical', APPROVED: 'good', PARTIALLY_APPROVED: 'good', REJECTED: 'neutral',
}
const label = (s: string) => s.toLowerCase().replace(/_/g, ' ')

function useAct(claimId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ path, body }: { path: string; body: unknown }) => api.post<Workspace>(path, body),
    onSuccess: d => { qc.setQueryData(['ws', claimId], d); qc.invalidateQueries({ queryKey: ['claims'] }); qc.invalidateQueries({ queryKey: ['notifications'] }) },
  })
}

type Mode = '' | 'OVERRIDE' | 'FALSE_POSITIVE' | 'REQUEST_EVIDENCE' | 'ESCALATE'

function FindingRowView({ f, ws }: { f: WsFinding; ws: Workspace }) {
  const act = useAct(ws.claim.claim_id)
  const [mode, setMode] = useState<Mode>('')
  const [text, setText] = useState('')
  const [amount, setAmount] = useState('')
  const [target, setTarget] = useState('SENIOR')
  const rec = f.recommendations.find(r => !r.superseded) ?? f.recommendations[f.recommendations.length - 1]
  const pending = f.decisions.find(d => d.status === 'PENDING_APPROVAL')
  const decide = (body: Record<string, unknown>) => act.mutate({ path: `/api/findings/${f.id}/decision`, body }, { onSuccess: () => { setMode(''); setText(''); setAmount('') } })
  const canDecide = ws.can.decide_findings && OPEN.has(f.status) && f.status !== 'PENDING_APPROVAL' && f.status !== 'ESCALATED'
  return (
    <div className="border-b border-line px-3 py-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <TypeBadge type={f.finding_type} /><SeverityBadge s={f.severity} /><StatusBadge s={f.status} /><Badge>{f.category_label}</Badge>
        <span className="num ml-auto text-[13px]">{f.amount_basis === 'ESTIMATED' ? '≈ ' : ''}{inr(f.effective_amount, 2)}</span><BasisBadge basis={f.amount_basis} />
      </div>
      <div className="mt-1.5 text-[13.5px] font-medium">{f.title}</div>
      <p className="mt-0.5 text-[12.5px] text-ink-2">{f.calculation}</p>

      {rec && (
        <div className="mt-2 grid gap-px border border-line bg-line sm:grid-cols-[minmax(0,1.6fr)_repeat(3,minmax(0,1fr))]">
          <div className="bg-panel px-2.5 py-2">
            <div className="eyebrow">Recommended correction · {rec.type.replace(/_/g, ' ').toLowerCase()} · v{rec.version}</div>
            <div className="mt-0.5 text-[12.5px] font-medium">{rec.action}</div>
            {rec.clause_text && <div className="mt-0.5 text-[11.5px] text-ink-3">{rec.rule_id}{rec.clause_ref ? ` · ${rec.clause_ref}` : ''}: {rec.clause_text}</div>}
          </div>
          <div className="bg-panel px-2.5 py-2"><div className="eyebrow">Original</div><div className="num text-[14px]">{inr(rec.original_amount, 2)}</div></div>
          <div className="bg-panel px-2.5 py-2"><div className="eyebrow">Corrected</div><div className="num text-[14px]">{inr(rec.corrected_amount, 2)}</div></div>
          <div className="bg-panel px-2.5 py-2"><div className="eyebrow">Payable impact</div><div className="num text-[14px]">−{inr(rec.settlement_impact, 2)}</div></div>
        </div>
      )}

      {canDecide && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          <Button variant="primary" disabled={act.isPending} onClick={() => decide({ decision: 'CONFIRM_DISCREPANCY' })}>Confirm discrepancy</Button>
          {rec && <Button disabled={act.isPending} onClick={() => decide({ decision: 'ACCEPT_CORRECTION' })}>Accept correction</Button>}
          {rec && <Button variant="ghost" onClick={() => { setMode('OVERRIDE'); setAmount(String(rec.settlement_impact ?? '')) }}>Override…</Button>}
          <Button variant="ghost" onClick={() => setMode('REQUEST_EVIDENCE')}>Request evidence…</Button>
          {ws.can.escalate && <Button variant="ghost" onClick={() => setMode('ESCALATE')}>Escalate…</Button>}
          <Button variant="danger" onClick={() => setMode('FALSE_POSITIVE')}>False positive…</Button>
        </div>
      )}
      {mode && (
        <form className="mt-2 grid gap-2 border-l-2 border-ink bg-surface p-2.5 sm:grid-cols-[minmax(0,1fr)_auto]" onSubmit={e => {
          e.preventDefault()
          if (mode === 'OVERRIDE') decide({ decision: 'ACCEPT_CORRECTION', override_impact: Number(amount), reason: text })
          else if (mode === 'FALSE_POSITIVE') decide({ decision: 'FALSE_POSITIVE', reason: text })
          else if (mode === 'REQUEST_EVIDENCE') decide({ decision: 'REQUEST_EVIDENCE', message: text, doc_types: ['INVOICE'] })
          else decide({ decision: 'ESCALATE', target, reason: text })
        }}>
          <div className="space-y-1.5">
            {mode === 'OVERRIDE' && <label className="flex items-center gap-2 text-[12px] text-ink-2">Payable impact you accept (₹)
              <input className={`${inputCls} num w-32`} type="number" min={0} step="0.01" required value={amount} onChange={e => setAmount(e.target.value)} /></label>}
            {mode === 'ESCALATE' && <select className={inputCls} value={target} onChange={e => setTarget(e.target.value)}>
              <option value="SENIOR">Senior claims reviewer</option><option value="SIU">Special Investigation Unit</option></select>}
            <textarea className={`${inputCls} h-auto w-full py-1.5`} rows={2} required value={text} onChange={e => setText(e.target.value)}
              placeholder={{ OVERRIDE: 'Reason for overriding (kept with the original recommendation)', FALSE_POSITIVE: 'Why this is not a discrepancy', REQUEST_EVIDENCE: 'What the policyholder should provide', ESCALATE: 'Why it needs escalation', '': '' }[mode]} />
            {mode === 'OVERRIDE' && <p className="text-[11px] text-ink-3">Overrides more than ₹10,000 away from the recommendation need senior approval.</p>}
          </div>
          <div className="flex items-start gap-1.5"><Button type="submit" variant="primary" disabled={act.isPending}>Save</Button><Button variant="ghost" onClick={() => setMode('')}>Cancel</Button></div>
        </form>
      )}
      {pending && (
        <div className="mt-2 border-l-2 border-warn bg-warn-bg px-2.5 py-2 text-[12px] text-warn">
          Override to <span className="num">{inr(pending.override_impact, 2)}</span> by {pending.user} awaits senior approval: “{pending.reason}”
          {ws.can.approve && <span className="ml-2 inline-flex gap-1.5">
            <Button onClick={() => { const r = window.prompt('Approval reason'); if (r) act.mutate({ path: `/api/finding-decisions/${pending.id}/approval`, body: { approve: true, reason: r } }) }}>Approve</Button>
            <Button variant="danger" onClick={() => { const r = window.prompt('Reason for declining'); if (r) act.mutate({ path: `/api/finding-decisions/${pending.id}/approval`, body: { approve: false, reason: r } }) }}>Decline</Button>
          </span>}
        </div>
      )}
      {f.decisions.length > 0 && (
        <ol className="mt-2 space-y-0.5 text-[11.5px] text-ink-2">
          {f.decisions.map(d => (
            <li key={d.id}><span className="num text-ink-3">{new Date(d.created_at).toLocaleString()}</span> · <b className="font-medium text-ink">{label(d.decision)}</b> by {d.user}
              {d.override_impact != null && <> · override {inr(d.override_impact, 2)} vs recommended {inr(d.recommended_impact, 2)}</>} · {label(d.status)}
              {d.reason && <> · “{d.reason}”</>}{d.approved_by && <> · {label(d.status)} by {d.approved_by}{d.approval_reason ? ` (“${d.approval_reason}”)` : ''}</>}</li>
          ))}
        </ol>
      )}
      {act.error && <p className="mt-1 text-[12px] text-critical">{(act.error as Error).message}</p>}
    </div>
  )
}

function ClaimDecisionPanel({ ws }: { ws: Workspace }) {
  const act = useAct(ws.claim.claim_id)
  const [decision, setDecision] = useState('PARTIALLY_APPROVED')
  const [amount, setAmount] = useState('')
  const [reason, setReason] = useState('')
  const pending = ws.decisions.find(d => d.status === 'PENDING_APPROVAL')
  const allowed = ws.can.decide_claim || ws.can.approve_clean
  const defaultAmount = ws.corrected_settlement ?? 0
  return (
    <Card code="D" title="Claim decision">
      <div className="grid grid-cols-3 gap-px border border-line bg-line">
        <div className="bg-panel px-2.5 py-2"><div className="eyebrow">Proposed</div><div className="num text-[15px]">{inr(ws.claim.proposed_settlement, 2)}</div></div>
        <div className="bg-panel px-2.5 py-2"><div className="eyebrow">Corrected</div><div className="num text-[15px]">{inr(ws.corrected_settlement, 2)}</div></div>
        <div className="bg-panel px-2.5 py-2"><div className="eyebrow">Engine expected</div><div className="num text-[15px]">{inr(ws.assessment?.expected_settlement, 2)}</div></div>
      </div>
      <p className="mt-1.5 text-[11.5px] text-ink-3">Corrected = proposed less every confirmed or corrected finding; dismissed findings are paid.</p>
      {ws.decisions.length > 0 && (
        <ol className="mt-2 space-y-0.5 text-[12px]">{ws.decisions.map(d => (
          <li key={d.id}><b className="font-medium">{label(d.decision)}</b> {d.approved_amount != null && <span className="num">{inr(d.approved_amount, 2)}</span>} · {d.user} · {label(d.status)}{d.approved_by ? ` by ${d.approved_by}` : ''} · “{d.reason}”</li>))}</ol>
      )}
      {pending && ws.state === 'PENDING_APPROVAL' && ws.can.approve && (
        <div className="mt-2 flex gap-1.5">
          <Button variant="primary" onClick={() => { const r = window.prompt('Approval reason'); if (r) act.mutate({ path: `/api/investigations/${ws.claim.claim_id}/approval`, body: { approve: true, reason: r } }) }}>Approve {label(pending.decision)}</Button>
          <Button variant="danger" onClick={() => { const r = window.prompt('Reason for sending back'); if (r) act.mutate({ path: `/api/investigations/${ws.claim.claim_id}/approval`, body: { approve: false, reason: r } }) }}>Send back</Button>
        </div>
      )}
      {allowed && (
        <form className="mt-3 space-y-2" onSubmit={e => { e.preventDefault(); act.mutate({ path: `/api/investigations/${ws.claim.claim_id}/decision`, body: { decision, reason, approved_amount: decision === 'REJECTED' ? undefined : Number(amount || defaultAmount) } }) }}>
          {ws.open_findings > 0 && <p className="text-[12px] text-warn">{ws.open_findings} finding(s) still need a decision.</p>}
          <div className="flex flex-wrap gap-1.5">
            <select className={inputCls} value={decision} onChange={e => setDecision(e.target.value)}>
              {ws.can.approve_clean && !ws.can.decide_claim ? <option value="APPROVED">Approve (fast-track)</option> : <>
                <option value="PARTIALLY_APPROVED">Approve with corrections</option><option value="APPROVED">Approve as proposed</option><option value="REJECTED">Reject</option></>}
            </select>
            {decision !== 'REJECTED' && <input className={`${inputCls} num w-36`} type="number" min={0} step="0.01" placeholder={String(decision === 'APPROVED' ? ws.claim.proposed_settlement ?? '' : defaultAmount)} value={amount} onChange={e => setAmount(e.target.value)} />}
          </div>
          <textarea className={`${inputCls} h-auto w-full py-1.5`} rows={2} required placeholder="Decision reason (shown in the audit trail)" value={reason} onChange={e => setReason(e.target.value)} />
          <div className="flex items-center gap-2"><Button type="submit" variant="primary" disabled={act.isPending || ws.open_findings > 0}>Record decision</Button>
            {ws.can.authority != null && <span className="eyebrow">your authority ≤ {inr(ws.can.authority)}</span>}</div>
        </form>
      )}
      {act.error && <p className="mt-1 text-[12px] text-critical">{(act.error as Error).message}</p>}
    </Card>
  )
}

function EvidenceTab({ ws }: { ws: Workspace }) {
  const [docId, setDocId] = useState<number | null>(ws.documents[0]?.id ?? null)
  const doc = ws.documents.find(d => d.id === docId)
  const rows = ws.assessment?.documents ?? []
  return (
    <div className="space-y-6">
      <Card code="E.1" title="Claim vs documents" flush>
        {rows.length === 0 ? <p className="p-3 text-[12.5px] text-ink-3">No supporting documents on file for this claim.</p> : (
          <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-[12.5px]">
            <thead className="border-b border-line bg-surface text-left text-ink-2"><tr><th className="px-3 py-1.5">Field</th><th className="px-3 py-1.5 text-right">Claim</th><th className="px-3 py-1.5 text-right">Document</th><th className="px-3 py-1.5">Status</th><th className="px-3 py-1.5">Source</th></tr></thead>
            <tbody>{rows.map((r, i) => (
              <tr key={i} className={`border-b border-line ${r.status === 'MATCH' ? '' : 'bg-critical-bg/60'}`}>
                <td className="num px-3 py-1.5 text-[12px]">{r.field}</td>
                <td className="num px-3 py-1.5 text-right">{typeof r.claim_value === 'number' ? inr(r.claim_value, 2) : r.claim_value ?? '—'}</td>
                <td className="num px-3 py-1.5 text-right">{typeof r.doc_value === 'number' ? inr(r.doc_value, 2) : r.doc_value ?? '—'}</td>
                <td className="px-3 py-1.5"><Badge tone={r.status === 'MATCH' ? 'good' : 'critical'}>{label(r.status)}</Badge></td>
                <td className="px-3 py-1.5 text-[11.5px] text-ink-2">{r.source ? <>{label(r.source.doc_type)}{r.source.page != null ? ` p.${r.source.page + 1}` : ''}{r.source.source_text ? <span className="num"> “{r.source.source_text}”</span> : ''} {r.source.verified ? '✓' : '(unverified)'}</> : '—'}</td>
              </tr>))}</tbody>
          </table></div>)}
      </Card>
      {ws.documents.length > 0 && (
        <Card code="E.2" title="Document viewer" actions={<select className={inputCls} value={docId ?? ''} onChange={e => setDocId(Number(e.target.value))}>
          {ws.documents.map(d => <option key={d.id} value={d.id}>{label(d.doc_type)} · {d.filename}</option>)}</select>}>
          {doc && <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <img className="w-full border border-line" src={authUrl(`/api/documents/${doc.id}/pages/0`)} alt={doc.filename} />
            <div className="max-h-[720px] overflow-y-auto"><table className="w-full text-[12px]">
              <thead className="sticky top-0 border-b border-line bg-surface text-left text-ink-2"><tr><th className="px-2 py-1">Field</th><th className="px-2 py-1">Value</th><th className="px-2 py-1">Source</th></tr></thead>
              <tbody>{doc.fields.map(f => (
                <tr key={f.id} className="border-b border-line align-top"><td className="num px-2 py-1 text-ink-2">{f.path}</td><td className="num px-2 py-1">{f.value}</td>
                  <td className="px-2 py-1 text-[11px] text-ink-3">{f.verified ? <span className="text-good">✓ p.{(f.page ?? 0) + 1}</span> : <span className="text-warn">unverified</span>} · {f.method}{f.source_text ? <div className="num">“{f.source_text}”</div> : null}</td></tr>))}</tbody>
            </table></div>
          </div>}
        </Card>
      )}
      <Card code="E.3" title="Evidence requests">
        {ws.evidence_requests.length === 0 ? <p className="text-[12.5px] text-ink-3">None.</p> : ws.evidence_requests.map(r => (
          <div key={r.id} className="border-b border-line py-2 text-[12.5px] last:border-0">
            <div className="flex items-center gap-2"><Badge tone={r.status === 'OPEN' ? 'warn' : 'good'}>{label(r.status)}</Badge><span>{r.message}</span><span className="num ml-auto text-ink-3">due {r.due_date} · by {r.requested_by}</span></div>
            {r.responses.map(x => <div key={x.id} className="mt-1 border-l-2 border-good pl-2 text-ink-2">{x.responder}: {x.message || '(documents)'} {x.doc_ids.length ? `· ${x.doc_ids.length} file(s)` : ''}</div>)}
          </div>))}
      </Card>
    </div>
  )
}

function RelatedTab({ ws }: { ws: Workspace }) {
  const n = ws.related.length
  const R = 130
  return (
    <div className="grid gap-6 xl:grid-cols-[360px_minmax(0,1fr)]">
      <Card code="L.1" title="Link map">
        {n === 0 ? <p className="text-[12.5px] text-ink-3">No related claims.</p> : (
          <svg viewBox="-180 -160 360 320" className="w-full" role="img" aria-label="Related claims graph">
            {ws.related.map((r, i) => {
              const a = (2 * Math.PI * i) / n - Math.PI / 2, x = R * Math.cos(a), y = R * Math.sin(a)
              const strong = r.links.some(l => l.strength === 'STRONG')
              return <g key={r.claim_id}>
                <line x1={0} y1={0} x2={x} y2={y} stroke={strong ? 'var(--critical)' : 'var(--ink-3)'} strokeWidth={strong ? 2 : 1} strokeDasharray={strong ? undefined : '4 3'} />
                <rect x={x - 44} y={y - 11} width={88} height={22} fill="var(--panel)" stroke="var(--line)" />
                <text x={x} y={y + 4} textAnchor="middle" fontSize="10" fontFamily="var(--font-mono)" fill="var(--ink)">{r.claim_id}</text>
              </g>
            })}
            <rect x={-48} y={-13} width={96} height={26} fill="var(--ink)" />
            <text x={0} y={4} textAnchor="middle" fontSize="10.5" fontFamily="var(--font-mono)" fill="var(--panel)">{ws.claim.claim_id}</text>
          </svg>)}
        <p className="text-[11px] text-ink-3">Solid red: strong link (invoice, payout account, document image, incident). Dashed: contact or service overlap.</p>
      </Card>
      <Card code="L.2" title="Related claims and matching attributes" flush>
        {ws.related.map(r => (
          <div key={r.claim_id} className="border-b border-line px-3 py-2.5 last:border-0">
            <div className="flex flex-wrap items-baseline gap-2"><Link className="num text-accent hover:underline" to={`/investigations/${r.claim_id}`}>{r.claim_id}</Link>
              <span className="text-[12.5px]">{r.claimant_name}</span><span className="num text-[11.5px] text-ink-3">policy {r.policy_no} · {r.provider_id} · {r.status?.toLowerCase()} · {inr(r.requested_amount)}</span></div>
            <ul className="mt-1 space-y-0.5">{r.links.map((l, i) => (
              <li key={i} className="text-[12px]"><Badge tone={l.strength === 'STRONG' ? 'critical' : l.strength === 'MEDIUM' ? 'warn' : 'neutral'}>{l.strength.toLowerCase()} · {l.attribute}</Badge> <span className="text-ink-2">{l.explanation}</span></li>))}</ul>
          </div>))}
        {n === 0 && <p className="p-3 text-[12.5px] text-ink-3">No shared invoices, accounts, contacts, incidents or images with other claims.</p>}
      </Card>
    </div>
  )
}

function PolicyTab({ ws }: { ws: Workspace }) {
  const p = ws.policy as Record<string, string | number> | null
  const flagged = new Set(ws.findings.filter(f => f.finding_type === 'CONFIRMED_VIOLATION').map(f => f.title))
  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
      <div className="space-y-6">
        {ws.assessment && ws.assessment.waterfall.length > 0 && <Card code="P.1" title="Settlement recalculation"><Waterfall a={ws.assessment} flaggedTitles={flagged} /></Card>}
        <Card code="P.2" title="Rules evaluated" flush>
          <table className="w-full text-[12.5px]"><tbody>{ws.assessment?.checks.map(c => (
            <tr key={c.rule_id} className="border-b border-line align-top"><td className="num w-14 px-3 py-1.5 text-ink-3">{c.rule_id}</td>
              <td className="px-3 py-1.5">{c.name}<div className="text-[11.5px] text-ink-3">{c.detail}</div></td>
              <td className="px-3 py-1.5"><Badge tone={c.outcome === 'FAIL' ? 'critical' : c.outcome === 'DEDUCTED' ? 'warn' : c.outcome === 'PASS' ? 'good' : 'neutral'}>{label(c.outcome)}</Badge></td></tr>))}</tbody></table>
        </Card>
      </div>
      <Card code="P.3" title="Policy terms">
        {p && <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1 text-[12.5px]">
          {[['Policy', `${p.policy_no} · ${p.plan}`], ['Status', p.status], ['Period', `${p.start_date} → ${p.end_date}`], ['Cover since', p.inception_date], ['Sum insured', inr(Number(p.sum_insured))],
            ['Deductible', inr(Number(p.deductible))], ['Co-payment', `${p.copay_pct}%`], ['Room / day', inr(Number(p.room_rent_limit))], ['ICU / day', inr(Number(p.icu_limit))]].map(([k, v]) => (
            <><dt className="text-ink-2" key={`${k}`}>{k}</dt><dd className="num text-right" key={`${k}v`}>{v}</dd></>))}
        </dl>}
        <div className="eyebrow mb-1 mt-4">Clauses cited in this claim</div>
        <ul className="space-y-1 text-[12px]">{[...new Set(ws.findings.flatMap(f => f.recommendations.map(r => r.rule_id)))].map(id => (
          <li key={id}><span className="num text-ink-3">{id}</span> {ws.rule_text[id] ?? ws.findings.flatMap(f => f.recommendations).find(r => r.rule_id === id)?.clause_text}</li>))}</ul>
      </Card>
    </div>
  )
}

function NotesTab({ ws }: { ws: Workspace }) {
  const act = useAct(ws.claim.claim_id)
  const [text, setText] = useState('')
  const [vis, setVis] = useState('INTERNAL')
  return (
    <Card code="N" title="Investigation notes">
      <ol className="space-y-2">{ws.notes.map(n => (
        <li key={n.id} className={`border-l-2 pl-2.5 text-[12.5px] ${n.visibility === 'SIU' ? 'border-critical' : 'border-line'}`}>
          <div className="num text-[11px] text-ink-3">{new Date(n.created_at).toLocaleString()} · {n.author} ({n.role.toLowerCase()}){n.visibility === 'SIU' ? ' · SIU only' : ''}</div>{n.text}</li>))}</ol>
      {ws.notes.length === 0 && <p className="text-[12.5px] text-ink-3">No notes yet.</p>}
      {ws.can.note && <form className="mt-3 space-y-2" onSubmit={e => { e.preventDefault(); act.mutate({ path: `/api/claims/${ws.claim.claim_id}/notes`, body: { text, visibility: vis } }, { onSuccess: () => setText('') }) }}>
        <textarea className={`${inputCls} h-auto w-full py-1.5`} rows={3} required value={text} onChange={e => setText(e.target.value)} placeholder="Observation, call summary, next step…" />
        <div className="flex gap-2">{ws.can.siu_notes && <select className={inputCls} value={vis} onChange={e => setVis(e.target.value)}><option value="INTERNAL">Internal</option><option value="SIU">SIU only</option></select>}
          <Button type="submit" variant="primary" disabled={act.isPending}>Add note</Button></div>
      </form>}
    </Card>
  )
}

function TimelineTab({ ws }: { ws: Workspace }) {
  return (
    <Card code="T" title={`Audit trail · ${ws.timeline.length} events`} flush>
      <div className="overflow-x-auto"><table className="w-full min-w-[820px] text-[12px]">
        <thead className="border-b border-line bg-surface text-left text-ink-2"><tr><th className="px-3 py-1.5">When</th><th className="px-3 py-1.5">Actor</th><th className="px-3 py-1.5">Action</th><th className="px-3 py-1.5">Detail</th><th className="px-3 py-1.5">Hash</th></tr></thead>
        <tbody>{ws.timeline.map(e => (
          <tr key={e.id} className="border-b border-line align-top">
            <td className="num whitespace-nowrap px-3 py-1.5 text-ink-3">{new Date(e.at).toLocaleString()}</td>
            <td className="px-3 py-1.5">{e.actor}<div className="eyebrow">{e.actor_role.toLowerCase()}</div></td>
            <td className="num px-3 py-1.5">{e.action.toLowerCase()}{e.ai_generated && <Badge tone="info">AI</Badge>}</td>
            <td className="px-3 py-1.5 text-ink-2">{e.reason && <div>“{e.reason}”</div>}{e.before && <div className="num text-[11px] text-ink-3">before {JSON.stringify(e.before).slice(0, 140)}</div>}{e.after && <div className="num text-[11px]">after {JSON.stringify(e.after).slice(0, 180)}</div>}</td>
            <td className="num px-3 py-1.5 text-[10.5px] text-ink-3">{e.hash.slice(0, 10)}…</td>
          </tr>))}</tbody>
      </table></div>
    </Card>
  )
}

export default function Investigation() {
  const { id = '' } = useParams()
  const { me } = useAuth()
  const [tab, setTab] = useState<(typeof TABS)[number]>(TABS[0])
  const q = useQuery({ queryKey: ['ws', id], queryFn: () => api.get<Workspace>(`/api/investigations/${id}`) })
  const act = useAct(id)
  return (
    <State loading={q.isLoading} error={q.error}>
      {q.data && (() => {
        const ws = q.data
        const calc = ws.findings.filter(f => f.amount_basis === 'CALCULATED' && f.status !== 'DISMISSED').reduce((a, f) => a + f.effective_amount, 0)
        const openEsc = ws.escalations.filter(e => e.status === 'OPEN')
        return (
          <div className="space-y-6">
            <div className="grid gap-4 border-b-2 border-rule pb-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
              <div className="min-w-0">
                <div className="eyebrow"><Link to="/queue" className="text-accent hover:underline">Investigations</Link> / {ws.claim.claim_type.toLowerCase()} · {ws.provider?.name}</div>
                <h1 className="mt-1 font-serif text-[28px] font-semibold leading-tight"><span className="num text-[24px]">{ws.claim.claim_id}</span> · {ws.claim.claimant_name}</h1>
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <Badge tone={STATE_TONE[ws.state] ?? 'neutral'}>{label(ws.state)}</Badge>
                  <span className="text-[12.5px] text-ink-2">assigned to <b className="font-medium text-ink">{ws.assigned_to ?? '—'}</b>{ws.assigned_to === me?.username ? ' (you)' : ''} · policyholder sees “{ws.portal_label}”</span>
                </div>
              </div>
              <div className="flex flex-wrap items-end gap-x-5 gap-y-2">
                <Meta label="Requested" value={inr(ws.claim.requested_amount)} />
                <Meta label="Proposed" value={inr(ws.claim.proposed_settlement)} />
                <Meta label="Calculated leakage" value={inr(calc)} />
                <Meta label="Corrected" value={inr(ws.corrected_settlement)} />
                {ws.can.export && <div className="flex gap-1.5">
                  <a href={authUrl(`/api/investigations/${id}/report.pdf`)}><Button>Report PDF</Button></a>
                  <a href={authUrl(`/api/investigations/${id}/report.json`)}><Button variant="ghost">JSON</Button></a></div>}
              </div>
            </div>

            {openEsc.map(e => (
              <div key={e.id} className="flex flex-wrap items-center gap-2 border-l-2 border-serious bg-serious-bg px-3 py-2 text-[12.5px]">
                <b className="font-medium">Escalated to {e.target === 'SIU' ? 'SIU' : 'senior review'}</b> by {e.raised_by}: “{e.reason}”
                {ws.can.resolve_escalation && me?.username !== e.raised_by && (e.target === 'SIU' ? me?.role === 'SIU' : me?.role === 'SENIOR') && <span className="ml-auto flex gap-1.5">
                  {e.target === 'SIU' ? <>
                    <Button onClick={() => { const r = window.prompt('SIU finding'); if (r) act.mutate({ path: `/api/escalations/${e.id}/resolve`, body: { resolution: r, outcome: 'SUBSTANTIATED' } }) }}>Substantiated</Button>
                    <Button onClick={() => { const r = window.prompt('SIU finding'); if (r) act.mutate({ path: `/api/escalations/${e.id}/resolve`, body: { resolution: r, outcome: 'NOT_SUBSTANTIATED' } }) }}>Not substantiated</Button>
                  </> : <Button onClick={() => { const r = window.prompt('Guidance for the analyst'); if (r) act.mutate({ path: `/api/escalations/${e.id}/resolve`, body: { resolution: r } }) }}>Resolve</Button>}
                  {ws.can.hold && <Button variant="danger" onClick={() => { const r = window.prompt('Reason to hold payment'); if (r) act.mutate({ path: `/api/investigations/${id}/decision`, body: { decision: 'ON_HOLD', reason: r } }) }}>Hold payment</Button>}
                </span>}
              </div>))}
            {act.error && <p className="text-[12.5px] text-critical">{(act.error as Error).message}</p>}

            <nav className="flex flex-wrap border-b border-line">{TABS.map(t => (
              <button key={t} onClick={() => setTab(t)} className={`-mb-px border-b-2 px-3 py-2 text-[12.5px] ${tab === t ? 'border-ink font-medium text-ink' : 'border-transparent text-ink-2 hover:text-ink'}`}>
                {t}{t === 'Findings & corrections' ? ` · ${ws.open_findings} open` : t === 'Related claims' ? ` · ${ws.related.length}` : t === 'Evidence' ? ` · ${ws.documents.length}` : ''}</button>))}
            </nav>

            {tab === 'Findings & corrections' && (
              <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_400px]">
                <Card code="F" title={`Findings · ${ws.findings.length}`} flush>
                  {ws.findings.length === 0 ? <p className="p-3 text-[12.5px] text-ink-3">No findings — the claim matches policy rules, history and documents.</p>
                    : ws.findings.map(f => <FindingRowView key={f.id} f={f} ws={ws} />)}
                </Card>
                <ClaimDecisionPanel ws={ws} />
              </div>)}
            {tab === 'Evidence' && <EvidenceTab ws={ws} />}
            {tab === 'Related claims' && <RelatedTab ws={ws} />}
            {tab === 'Policy & rules' && <PolicyTab ws={ws} />}
            {tab === 'Notes' && <NotesTab ws={ws} />}
            {tab === 'Timeline' && <TimelineTab ws={ws} />}
          </div>
        )
      })()}
    </State>
  )
}
