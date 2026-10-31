import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { Link, Navigate, Route, Routes, useNavigate, useParams } from 'react-router-dom'
import { api, authUrl, inr, type Extraction } from '../api'
import { useAuth } from '../auth'
import { Badge, Button, inputCls, State, type Tone } from '../components/ui'

interface ClaimSummary { claim_id: string; patient: string; claim_type: string; hospital: string; incident_date: string; submitted_date: string; amount_claimed: number; status: string; next_step: string; action_needed: number; approved_amount: number | null }
interface Home {
  name: string
  policy: { policy_no: string; plan: string; status: string; start_date: string; end_date: string; sum_insured: number; used: number; remaining: number; deductible: number; copay_pct: number; room_rent_limit: number } | null
  members: { member_id: string; name: string; relationship: string }[]
  claims: ClaimSummary[]
}
interface ClaimView extends ClaimSummary {
  state: string
  timeline: { at: string; status: string }[]
  admission_date: string | null
  discharge_date: string | null
  invoice_no: string | null
  lines: { description: string; date: string; quantity: number; amount: number }[]
  documents: { id: number; filename: string; type: string; uploaded: string; extracted: Extraction | null }[]
  requests: { id: number; message: string; doc_types: string[]; due_date: string; status: string; asked_on: string }[]
  letter: { decision: string; decided_on: string; amount_claimed: number; approved_amount: number; adjustments: { explanation: string; amount_not_payable: number | null }[]; paid: boolean; payout_account: string | null; can_request_reconsideration: boolean } | null
}

const STATUS_TONE: Record<string, Tone> = { Draft: 'neutral', Submitted: 'info', 'Under review': 'info', 'Action needed': 'warn', 'Decision made': 'good', Paid: 'good' }
const STEPS = ['Submitted', 'Under review', 'Decision made', 'Paid']

function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="border-t-2 border-rule bg-panel">
      <header className="flex items-center justify-between border-b border-line px-4 py-2.5">
        <h2 className="font-mono text-[11px] font-medium uppercase tracking-[0.07em]">{title}</h2>{aside}
      </header>
      <div className="p-4">{children}</div>
    </section>
  )
}

function PortalHome() {
  const q = useQuery({ queryKey: ['portal-me'], queryFn: () => api.get<Home>('/api/portal/me') })
  return (
    <State loading={q.isLoading} error={q.error}>
      {q.data && (() => {
        const h = q.data
        const actions = h.claims.filter(c => c.action_needed > 0)
        const p = h.policy
        return (
          <div className="space-y-6">
            <div className="flex flex-wrap items-end justify-between gap-4 border-b-2 border-rule pb-4">
              <div>
                <div className="eyebrow">Your health cover</div>
                <h1 className="mt-1 font-serif text-[28px] font-semibold leading-tight">Hello, {h.name.split(' ')[0]}</h1>
                {p && <p className="text-[13px] text-ink-2">{p.plan.toLowerCase()} plan · policy <span className="num">{p.policy_no}</span> · {p.status.toLowerCase()} until {p.end_date}</p>}
              </div>
              <Link to="/portal/new"><Button variant="primary">File a claim</Button></Link>
            </div>
            {actions.length > 0 && (
              <div className="border-l-2 border-warn bg-warn-bg px-4 py-3">
                <div className="eyebrow !text-warn">Action needed</div>
                {actions.map(c => <Link key={c.claim_id} to={`/portal/claims/${c.claim_id}`} className="mt-1 block text-[13px] text-warn underline">{c.claim_id} · {c.patient}: we need more information</Link>)}
              </div>
            )}
            {p && (
              <div className="grid grid-cols-2 gap-px border border-line bg-line sm:grid-cols-4">
                {[['Sum insured', inr(p.sum_insured)], ['Used this year', inr(p.used)], ['Remaining', inr(p.remaining)], ['Room limit / day', inr(p.room_rent_limit)]].map(([k, v]) => (
                  <div key={k} className="bg-panel px-3 py-2.5"><div className="eyebrow">{k}</div><div className="num mt-1 text-[18px]">{v}</div></div>))}
              </div>
            )}
            <Section title="Your claims" aside={<span className="eyebrow">{h.claims.length}</span>}>
              <ul className="-my-1">
                {h.claims.map(c => (
                  <li key={c.claim_id} className="border-b border-line last:border-0">
                    <Link to={`/portal/claims/${c.claim_id}`} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 py-2.5 hover:bg-surface">
                      <span><span className="num text-[12px] text-ink-3">{c.claim_id}</span> <span className="text-[13.5px]">{c.patient}</span>
                        <span className="block text-[12px] text-ink-2">{c.hospital} · {c.incident_date} · {c.next_step}</span></span>
                      <span className="text-right"><Badge tone={STATUS_TONE[c.status] ?? 'neutral'}>{c.status}</Badge>
                        <span className="num block text-[12.5px]">{c.approved_amount != null ? `${inr(c.approved_amount)} approved` : inr(c.amount_claimed)}</span></span>
                    </Link>
                  </li>))}
                {h.claims.length === 0 && <li className="text-[13px] text-ink-3">No claims yet.</li>}
              </ul>
            </Section>
            <Section title="Covered members">
              <ul className="text-[13px]">{h.members.map(m => <li key={m.member_id} className="flex justify-between border-b border-line py-1.5 last:border-0"><span>{m.name}</span><span className="text-ink-3">{m.relationship.toLowerCase()} · <span className="num">{m.member_id}</span></span></li>)}</ul>
            </Section>
          </div>
        )
      })()}
    </State>
  )
}

type Line = { description: string; service_code: string | null; category: string; date: string; quantity: number; unit_price: number; amount: number }

function NewClaim() {
  const nav = useNavigate()
  const qc = useQueryClient()
  const home = useQuery({ queryKey: ['portal-me'], queryFn: () => api.get<Home>('/api/portal/me') })
  const hospitals = useQuery({ queryKey: ['portal-providers'], queryFn: () => api.get<{ provider_id: string; name: string; city: string; type: string }[]>('/api/portal/providers') })
  const [step, setStep] = useState(1)
  const [form, setForm] = useState({ member_id: '', claim_type: 'INPATIENT', provider_id: '', admission_date: '', discharge_date: '', description: '', payout_account: '' })
  const [claim, setClaim] = useState<ClaimView | null>(null)
  const [lines, setLines] = useState<Line[]>([])
  const [meta, setMeta] = useState({ invoice_no: '', diagnosis_code: '' })
  const [declared, setDeclared] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [docType, setDocType] = useState('INVOICE')
  const set = (k: string, v: string) => setForm(f => ({ ...f, [k]: v }))
  const start = useMutation({
    mutationFn: () => api.post<ClaimView>('/api/portal/claims', { ...form, discharge_date: form.discharge_date || null }),
    onSuccess: c => { setClaim(c); setStep(2) },
  })
  const upload = useMutation({
    mutationFn: () => { const fd = new FormData(); fd.append('file', file!); fd.append('doc_type', docType); return api.form<ClaimView>(`/api/portal/claims/${claim!.claim_id}/documents`, fd) },
    onSuccess: c => {
      setClaim(c); setFile(null)
      const inv = c.documents.find(d => d.type === 'INVOICE' && d.extracted)?.extracted
      if (inv && lines.length === 0) {
        setLines(inv.lines.map(l => ({ description: l.description, service_code: l.service_code, category: l.category, date: l.service_date || form.admission_date, quantity: l.quantity, unit_price: l.unit_price, amount: l.amount })))
        setMeta({ invoice_no: inv.invoice_no ?? '', diagnosis_code: inv.diagnosis_code ?? '' })
      }
    },
  })
  const submit = useMutation({
    mutationFn: () => api.post<ClaimView>(`/api/portal/claims/${claim!.claim_id}/submit`, { lines, ...meta, declaration: declared }),
    onSuccess: c => { qc.invalidateQueries({ queryKey: ['portal-me'] }); nav(`/portal/claims/${c.claim_id}`) },
  })
  const err = (start.error || upload.error || submit.error) as Error | null
  const total = lines.reduce((a, l) => a + Number(l.amount || 0), 0)
  return (
    <div className="space-y-6">
      <div className="border-b-2 border-rule pb-4">
        <div className="eyebrow"><Link to="/portal" className="text-accent">Home</Link> / New claim</div>
        <h1 className="mt-1 font-serif text-[28px] font-semibold">File a claim</h1>
        <ol className="mt-3 grid grid-cols-3 gap-px border border-line bg-line text-[12px]">
          {['About the treatment', 'Upload your bills', 'Review & submit'].map((t, i) => (
            <li key={t} className={`px-3 py-2 ${step === i + 1 ? 'bg-ink text-panel' : step > i + 1 ? 'bg-surface' : 'bg-panel text-ink-3'}`}><span className="num">{i + 1}</span> · {t}</li>))}
        </ol>
      </div>
      {err && <p className="border-l-2 border-critical bg-critical-bg px-3 py-2 text-[12.5px] text-critical">{err.message}</p>}

      {step === 1 && (
        <Section title="About the treatment">
          <form className="grid gap-3 sm:grid-cols-2" onSubmit={e => { e.preventDefault(); start.mutate() }}>
            <label className="text-[12px] text-ink-2">Who was treated
              <select required className={`${inputCls} mt-1 w-full`} value={form.member_id} onChange={e => set('member_id', e.target.value)}>
                <option value="">Choose…</option>{home.data?.members.map(m => <option key={m.member_id} value={m.member_id}>{m.name} ({m.relationship.toLowerCase()})</option>)}</select></label>
            <label className="text-[12px] text-ink-2">Type of claim
              <select className={`${inputCls} mt-1 w-full`} value={form.claim_type} onChange={e => set('claim_type', e.target.value)}>
                {[['INPATIENT', 'Hospital stay'], ['DAYCARE', 'Day-care procedure'], ['OUTPATIENT', 'Consultation / tests'], ['PHYSIO', 'Physiotherapy'], ['DIALYSIS', 'Dialysis']].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
            <label className="text-[12px] text-ink-2 sm:col-span-2">Hospital or clinic
              <select required className={`${inputCls} mt-1 w-full`} value={form.provider_id} onChange={e => set('provider_id', e.target.value)}>
                <option value="">Choose a network hospital…</option>{hospitals.data?.map(h => <option key={h.provider_id} value={h.provider_id}>{h.name} · {h.city}</option>)}</select></label>
            <label className="text-[12px] text-ink-2">{form.claim_type === 'INPATIENT' || form.claim_type === 'DAYCARE' ? 'Admitted on' : 'Date of treatment'}
              <input required type="date" className={`${inputCls} mt-1 w-full`} value={form.admission_date} onChange={e => set('admission_date', e.target.value)} /></label>
            {(form.claim_type === 'INPATIENT' || form.claim_type === 'DAYCARE') && <label className="text-[12px] text-ink-2">Discharged on
              <input type="date" className={`${inputCls} mt-1 w-full`} value={form.discharge_date} onChange={e => set('discharge_date', e.target.value)} /></label>}
            <label className="text-[12px] text-ink-2 sm:col-span-2">What happened (optional)
              <textarea className={`${inputCls} mt-1 h-auto w-full py-1.5`} rows={2} value={form.description} onChange={e => set('description', e.target.value)} /></label>
            <label className="text-[12px] text-ink-2 sm:col-span-2">Bank account for payment (IFSC and account)
              <input className={`${inputCls} num mt-1 w-full`} placeholder="HDFC0001234 XXXXXX1234" value={form.payout_account} onChange={e => set('payout_account', e.target.value)} /></label>
            <div className="sm:col-span-2"><Button type="submit" variant="primary" disabled={start.isPending}>{start.isPending ? 'Saving…' : 'Continue'}</Button></div>
          </form>
        </Section>
      )}

      {step === 2 && claim && (
        <Section title="Upload your bills" aside={<span className="num text-[11px] text-ink-3">{claim.claim_id} · draft</span>}>
          <p className="text-[13px] text-ink-2">Upload the hospital's final bill first — we read the items for you to check. Add the discharge summary, prescriptions and reports too.</p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <select className={inputCls} value={docType} onChange={e => setDocType(e.target.value)}>
              <option value="INVOICE">Hospital bill</option><option value="DISCHARGE_SUMMARY">Discharge summary</option><option value="PRESCRIPTION">Prescription</option><option value="REPORT">Test report</option></select>
            <label className="flex h-8 cursor-pointer items-stretch border border-line text-[12.5px] hover:border-ink">
              <span className="flex items-center border-r border-line bg-surface px-2.5 font-medium">Browse…</span>
              <span className="flex min-w-0 items-center truncate px-2 text-ink-3">{file?.name ?? 'PDF, JPG or PNG'}</span>
              <input className="sr-only" type="file" accept=".pdf,.png,.jpg,.jpeg" onChange={e => setFile(e.target.files?.[0] ?? null)} /></label>
            <Button variant="primary" disabled={!file || upload.isPending} onClick={() => upload.mutate()}>{upload.isPending ? 'Reading document…' : 'Upload'}</Button>
          </div>
          {claim.documents.length > 0 && <ul className="mt-3 text-[12.5px]">{claim.documents.map(d => <li key={d.id} className="border-b border-line py-1">✓ {d.filename} <span className="text-ink-3">· {d.type.toLowerCase().replace('_', ' ')}</span></li>)}</ul>}
          <div className="mt-4 flex gap-2"><Button variant="primary" onClick={() => setStep(3)} disabled={claim.documents.length === 0}>Continue</Button>
            <Button variant="ghost" onClick={() => { setLines([{ description: '', service_code: null, category: 'other', date: form.admission_date, quantity: 1, unit_price: 0, amount: 0 }]); setStep(3) }}>Enter items manually</Button></div>
        </Section>
      )}

      {step === 3 && claim && (
        <Section title="Review & submit" aside={<span className="num text-[11px] text-ink-3">{claim.claim_id}</span>}>
          <p className="text-[13px] text-ink-2">Check the items read from your bill. Correct anything that doesn't match the paper bill.</p>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            <label className="text-[12px] text-ink-2">Bill number<input className={`${inputCls} num mt-1 w-full`} value={meta.invoice_no} onChange={e => setMeta(m => ({ ...m, invoice_no: e.target.value }))} /></label>
            <label className="text-[12px] text-ink-2">Diagnosis code (if on the bill)<input className={`${inputCls} num mt-1 w-full`} value={meta.diagnosis_code} onChange={e => setMeta(m => ({ ...m, diagnosis_code: e.target.value }))} /></label>
          </div>
          <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[560px] text-[12.5px]">
            <thead className="border-b border-line text-left text-ink-2"><tr><th className="py-1 pr-2">Item</th><th className="py-1 pr-2">Date</th><th className="py-1 pr-2 text-right">Qty</th><th className="py-1 pr-2 text-right">Rate</th><th className="py-1 text-right">Amount</th><th /></tr></thead>
            <tbody>{lines.map((l, i) => (
              <tr key={i} className="border-b border-line">
                <td className="py-1 pr-2"><input className={`${inputCls} w-full`} value={l.description} onChange={e => setLines(ls => ls.map((x, j) => j === i ? { ...x, description: e.target.value } : x))} /></td>
                <td className="py-1 pr-2"><input type="date" className={`${inputCls} num`} value={l.date} onChange={e => setLines(ls => ls.map((x, j) => j === i ? { ...x, date: e.target.value } : x))} /></td>
                <td className="py-1 pr-2"><input type="number" min={0} className={`${inputCls} num w-16 text-right`} value={l.quantity} onChange={e => setLines(ls => ls.map((x, j) => j === i ? { ...x, quantity: Number(e.target.value), amount: Number(e.target.value) * x.unit_price } : x))} /></td>
                <td className="py-1 pr-2"><input type="number" min={0} className={`${inputCls} num w-24 text-right`} value={l.unit_price} onChange={e => setLines(ls => ls.map((x, j) => j === i ? { ...x, unit_price: Number(e.target.value), amount: x.quantity * Number(e.target.value) } : x))} /></td>
                <td className="num py-1 text-right">{inr(l.amount, 2)}</td>
                <td className="py-1 pl-2"><button className="text-ink-3 hover:text-critical" onClick={() => setLines(ls => ls.filter((_, j) => j !== i))} aria-label="Remove item">✕</button></td>
              </tr>))}</tbody>
          </table></div>
          <div className="mt-2 flex items-center justify-between">
            <Button variant="ghost" onClick={() => setLines(ls => [...ls, { description: '', service_code: null, category: 'other', date: form.admission_date, quantity: 1, unit_price: 0, amount: 0 }])}>+ Add item</Button>
            <span className="text-[13px]">Total claimed <b className="num">{inr(total, 2)}</b></span>
          </div>
          <label className="mt-4 flex items-start gap-2 text-[12.5px]"><input type="checkbox" className="mt-0.5" checked={declared} onChange={e => setDeclared(e.target.checked)} />
            I confirm the information and documents are true and complete, and that this treatment has not been claimed elsewhere.</label>
          <div className="mt-3 flex gap-2"><Button variant="primary" disabled={!declared || lines.length === 0 || submit.isPending} onClick={() => submit.mutate()}>{submit.isPending ? 'Submitting & checking…' : 'Submit claim'}</Button>
            <Button variant="ghost" onClick={() => setStep(2)}>Back</Button></div>
        </Section>
      )}
    </div>
  )
}

function ClaimPage() {
  const { id = '' } = useParams()
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['portal-claim', id], queryFn: () => api.get<ClaimView>(`/api/portal/claims/${id}`) })
  const [msg, setMsg] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [why, setWhy] = useState('')
  const respond = useMutation({
    mutationFn: (reqId: number) => { const fd = new FormData(); fd.append('message', msg); files.forEach(f => fd.append('files', f)); return api.form<ClaimView>(`/api/portal/requests/${reqId}/respond`, fd) },
    onSuccess: d => { qc.setQueryData(['portal-claim', id], d); qc.invalidateQueries({ queryKey: ['portal-me'] }); setMsg(''); setFiles([]) },
  })
  const reconsider = useMutation({
    mutationFn: () => api.post<ClaimView>(`/api/portal/claims/${id}/reconsideration`, { reason: why }),
    onSuccess: d => { qc.setQueryData(['portal-claim', id], d); setWhy('') },
  })
  return (
    <State loading={q.isLoading} error={q.error}>
      {q.data && (() => {
        const c = q.data
        const reached = new Set(c.timeline.map(t => t.status))
        const open = c.requests.filter(r => r.status === 'OPEN')
        return (
          <div className="space-y-6">
            <div className="border-b-2 border-rule pb-4">
              <div className="eyebrow"><Link to="/portal" className="text-accent">Home</Link> / Claim <span className="num">{c.claim_id}</span></div>
              <h1 className="mt-1 font-serif text-[26px] font-semibold leading-tight">{c.patient} · {c.hospital}</h1>
              <p className="text-[13px] text-ink-2">{c.incident_date} · {inr(c.amount_claimed, 2)} claimed · <Badge tone={STATUS_TONE[c.status] ?? 'neutral'}>{c.status}</Badge></p>
            </div>
            <ol className="grid grid-cols-4 gap-px border border-line bg-line">
              {STEPS.map(s => {
                const at = c.timeline.find(t => t.status === s)?.at
                const current = c.status === s || (s === 'Under review' && c.status === 'Action needed')
                return <li key={s} className={`px-3 py-2 text-[12px] ${current ? 'bg-ink text-panel' : reached.has(s) ? 'bg-surface' : 'bg-panel text-ink-3'}`}>
                  {reached.has(s) || current ? '●' : '○'} {s}<div className={`num text-[10.5px] ${current ? 'text-panel/70' : 'text-ink-3'}`}>{at ? new Date(at).toLocaleDateString() : '—'}</div></li>
              })}
            </ol>
            <p className="border-l-2 border-ink pl-3 text-[13px]"><b className="font-medium">Next:</b> {c.next_step}</p>

            {open.map(r => (
              <Section key={r.id} title="We need more information" aside={<span className="num text-[11px] text-warn">reply by {r.due_date}</span>}>
                <p className="text-[13.5px]">{r.message}</p>
                {r.doc_types.length > 0 && <p className="mt-1 text-[12px] text-ink-3">Requested: {r.doc_types.map(t => t.toLowerCase().replace('_', ' ')).join(', ')}</p>}
                <textarea className={`${inputCls} mt-3 h-auto w-full py-1.5`} rows={2} placeholder="Your reply (optional if you attach documents)" value={msg} onChange={e => setMsg(e.target.value)} />
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <label className="flex h-8 cursor-pointer items-stretch border border-line text-[12.5px] hover:border-ink">
                    <span className="flex items-center border-r border-line bg-surface px-2.5 font-medium">Attach…</span>
                    <span className="flex items-center px-2 text-ink-3">{files.length ? `${files.length} file(s)` : 'PDF, JPG or PNG'}</span>
                    <input className="sr-only" type="file" multiple accept=".pdf,.png,.jpg,.jpeg" onChange={e => setFiles([...(e.target.files ?? [])])} /></label>
                  <Button variant="primary" disabled={respond.isPending || (!msg.trim() && files.length === 0)} onClick={() => respond.mutate(r.id)}>{respond.isPending ? 'Sending…' : 'Send'}</Button>
                </div>
                {respond.error && <p className="mt-1 text-[12px] text-critical">{(respond.error as Error).message}</p>}
              </Section>))}

            {c.letter && (
              <Section title="Decision" aside={<a href={authUrl(`/api/portal/claims/${c.claim_id}/letter.pdf`)} className="font-mono text-[11px] uppercase text-accent">Download letter</a>}>
                <div className="font-serif text-[20px]">{c.letter.decision}</div>
                <table className="mt-3 w-full text-[13px]"><tbody>
                  <tr className="border-b border-line"><td className="py-1.5">Amount claimed</td><td className="num py-1.5 text-right">{inr(c.letter.amount_claimed, 2)}</td></tr>
                  {c.letter.adjustments.map((a, i) => <tr key={i} className="border-b border-line align-top"><td className="py-1.5 pr-3 text-ink-2">{a.explanation}</td><td className="num py-1.5 text-right text-ink-2">−{inr(a.amount_not_payable, 2)}</td></tr>)}
                  <tr className="border-t-2 border-rule"><td className="py-1.5 font-medium">Approved amount</td><td className="num py-1.5 text-right text-[16px] font-medium">{inr(c.letter.approved_amount, 2)}</td></tr>
                </tbody></table>
                <p className="mt-2 text-[12px] text-ink-3">Line amounts are as billed; the approved amount also reflects your deductible and co-payment. Payment to {c.letter.payout_account ?? 'your registered account'} · {c.letter.paid ? 'paid' : 'in process'}.</p>
                {c.letter.can_request_reconsideration && (
                  <div className="mt-4 border-t border-line pt-3">
                    <div className="eyebrow mb-1">Disagree with the decision?</div>
                    <textarea className={`${inputCls} h-auto w-full py-1.5`} rows={2} value={why} onChange={e => setWhy(e.target.value)} placeholder="Tell us why — for example, the hospital had no room in your category" />
                    <Button disabled={!why.trim() || reconsider.isPending} onClick={() => reconsider.mutate()}>Request reconsideration</Button>
                  </div>)}
              </Section>
            )}

            <Section title="What you claimed">
              <table className="w-full text-[12.5px]"><tbody>{c.lines.map((l, i) => <tr key={i} className="border-b border-line"><td className="py-1">{l.description}</td><td className="num py-1 text-ink-3">{l.date}</td><td className="num py-1 text-right">{inr(l.amount, 2)}</td></tr>)}</tbody></table>
              <div className="eyebrow mb-1 mt-4">Documents</div>
              <ul className="text-[12.5px]">{c.documents.map(d => <li key={d.id}>✓ {d.filename} <span className="text-ink-3">· {d.type.toLowerCase().replace('_', ' ')} · {new Date(d.uploaded).toLocaleDateString()}</span></li>)}</ul>
            </Section>
            <Section title="History">
              <ol className="text-[12.5px]">{c.timeline.map((t, i) => <li key={i} className="flex justify-between border-b border-line py-1 last:border-0"><span>{t.status}</span><span className="num text-ink-3">{new Date(t.at).toLocaleString()}</span></li>)}</ol>
            </Section>
          </div>
        )
      })()}
    </State>
  )
}

export default function Portal() {
  const { me, signOut } = useAuth()
  return (
    <div className="min-h-screen">
      <header className="border-b-2 border-rule bg-panel">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-3">
          <Link to="/portal" className="flex items-center gap-2"><span className="grid h-7 w-7 place-items-center border border-ink font-mono text-[11px] font-semibold">CS</span><span className="font-serif text-[16px] font-semibold">Claims Sentinel</span><span className="eyebrow hidden sm:inline">· my claims</span></Link>
          <div className="flex items-center gap-3 text-[12.5px]"><span className="text-ink-2">{me?.full_name}</span><button className="text-accent hover:underline" onClick={signOut}>Sign out</button></div>
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-4 py-6">
        <Routes>
          <Route path="/portal" element={<PortalHome />} />
          <Route path="/portal/new" element={<NewClaim />} />
          <Route path="/portal/claims/:id" element={<ClaimPage />} />
          <Route path="*" element={<Navigate to="/portal" replace />} />
        </Routes>
      </main>
    </div>
  )
}
