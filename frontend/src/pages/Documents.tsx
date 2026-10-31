import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, inr, type DocumentRec, type Extraction, type FindingRow } from '../api'
import { Badge, Button, Card, PageHeader, State, TypeBadge, inputCls, useMeta } from '../components/ui'

const HEADER_FIELDS: [keyof Extraction, string][] = [
  ['invoice_no', 'Invoice no.'], ['invoice_date', 'Invoice date'], ['provider_id', 'Provider ID'], ['provider_name', 'Provider'],
  ['patient_name', 'Patient'], ['member_id', 'Member ID'], ['policy_no', 'Policy no.'], ['claim_type', 'Claim type'],
  ['admission_date', 'Admission'], ['discharge_date', 'Discharge'], ['diagnosis_code', 'Diagnosis (ICD-10)'],
]

interface ConfirmResult { ok: boolean; claim_id: string | null; errors: { field: string | null; message: string; severity: string }[]; findings?: FindingRow[] }

function Review({ doc }: { doc: DocumentRec }) {
  const qc = useQueryClient()
  const [ext, setExt] = useState<Extraction>(doc.extraction!)
  const [proposed, setProposed] = useState('')
  useEffect(() => setExt(doc.extraction!), [doc])
  const confirm = useMutation({
    mutationFn: () => api.post<ConfirmResult>(`/api/documents/${doc.id}/confirm`, { extraction: ext, proposed_settlement: proposed === '' ? null : Number(proposed) }),
    onSuccess: () => qc.invalidateQueries(),
  })
  const setField = (k: keyof Extraction, v: string) => setExt({ ...ext, [k]: v === '' ? null : v })
  const setLine = (i: number, k: string, v: string) => {
    const lines = ext.lines.map((l, j) => j !== i ? l : { ...l, [k]: ['quantity', 'unit_price', 'amount'].includes(k) ? Number(v) : v })
    setExt({ ...ext, lines })
  }
  const total = ext.lines.reduce((s, l) => s + Number(l.amount || 0), 0)
  const done = doc.status === 'CONFIRMED' || confirm.data?.ok
  return (
    <Card code="D.2" title={`Review extraction · ${doc.filename}`} actions={<Badge tone="info">{ext.document_type}</Badge>}>
      {ext.lines.length === 0 && ext.document_type === 'other' && (
        <div className="mb-3 border-l-2 border-critical bg-critical-bg px-3 py-2 text-sm text-critical">
          This doesn't look like a hospital bill or discharge summary — no claim data was found, and none was invented. Upload an invoice to create a claim.
        </div>
      )}
      {ext.missing_fields.length > 0 && ext.document_type !== 'other' && (
        <div className="mb-3 border-l-2 border-warn bg-warn-bg px-3 py-2 text-[12.5px] text-warn">Not found on the document: {ext.missing_fields.join(', ')}. Missing values stay empty — the engine will ask for them rather than assume.</div>
      )}
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {HEADER_FIELDS.map(([k, label]) => (
          <label key={k} className="text-xs text-ink-2">{label}
            <input className={`${inputCls} mt-0.5 w-full`} disabled={!!done} value={(ext[k] as string | null) ?? ''} onChange={e => setField(k, e.target.value)} />
          </label>
        ))}
      </div>
      <div className="-mx-4 mt-4 overflow-x-auto">
        <table className="w-full min-w-[760px] text-[12.5px]">
          <thead className="border-b border-line bg-surface text-left text-ink-2">
            <tr><th className="px-3 py-2">Code</th><th className="px-3 py-2">Description</th><th className="px-3 py-2">Date</th><th className="px-3 py-2">Category</th>
              <th className="px-3 py-2 text-right">Qty</th><th className="px-3 py-2 text-right">Rate</th><th className="px-3 py-2 text-right">Amount</th></tr>
          </thead>
          <tbody>
            {ext.lines.map((l, i) => (
              <tr key={i} className="border-t border-line">
                {(['service_code', 'description', 'service_date', 'category', 'quantity', 'unit_price', 'amount'] as const).map(k => (
                  <td key={k} className="px-1 py-1">
                    <input className={`${inputCls} w-full ${['quantity', 'unit_price', 'amount'].includes(k) ? 'text-right' : ''}`} disabled={!!done}
                      value={String(l[k] ?? '')} onChange={e => setLine(i, k, e.target.value)} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex flex-wrap items-end justify-between gap-3">
        <div className="text-[12.5px] text-ink-2">Lines total <b className="tabular text-ink">{inr(total, 2)}</b>{ext.total_amount != null && Math.abs(ext.total_amount - total) > 1 && <span className="ml-2 text-critical">≠ document total {inr(ext.total_amount, 2)}</span>}</div>
        {!done && (
          <div className="flex items-end gap-2">
            <label className="text-xs text-ink-2">Proposed settlement (optional)
              <input className={`${inputCls} mt-0.5 block w-40`} type="number" min={0} value={proposed} onChange={e => setProposed(e.target.value)} />
            </label>
            <Button variant="primary" disabled={confirm.isPending || ext.lines.length === 0} title={ext.lines.length === 0 ? 'No invoice lines to create a claim from' : undefined} onClick={() => confirm.mutate()}>{confirm.isPending ? 'Validating & analysing…' : 'Create claim & analyse'}</Button>
          </div>
        )}
      </div>
      {confirm.error && <p className="mt-2 text-[12.5px] text-critical">{(confirm.error as Error).message}</p>}
      {confirm.data && (
        <div className="mt-4 space-y-2 border-t border-line pt-3 text-[12.5px]">
          {confirm.data.ok ? <p className="text-good">Created claim <b>{confirm.data.claim_id}</b> and re-ran the analysis.</p>
            : <p className="text-critical">The claim was rejected by validation:</p>}
          {confirm.data.errors.map((e, i) => <div key={i} className="text-xs">{e.severity === 'ERROR' ? '✕' : '!'} {e.field}: {e.message}</div>)}
          {confirm.data.findings?.map(f => (
            <div key={f.id} className="flex items-center gap-2"><TypeBadge type={f.finding_type} /><Link className="text-accent" to={`/alerts/${f.id}`}>{f.title}</Link><span className="tabular text-ink-3">{inr(f.leakage_amount)}</span></div>
          ))}
          {confirm.data.ok && confirm.data.findings?.length === 0 && <p className="text-ink-2">No leakage found on this claim.</p>}
        </div>
      )}
      {doc.status === 'CONFIRMED' && !confirm.data && <p className="mt-3 text-[12.5px] text-ink-2">Already created as {doc.claim_id}.</p>}
    </Card>
  )
}

export default function Documents() {
  const qc = useQueryClient()
  const { data: meta } = useMeta()
  const docs = useQuery({ queryKey: ['documents'], queryFn: () => api.get<DocumentRec[]>('/api/documents') })
  const [selected, setSelected] = useState<number | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const upload = useMutation({
    mutationFn: () => api.upload<DocumentRec>('/api/documents', file!),
    onSuccess: d => { qc.invalidateQueries({ queryKey: ['documents'] }); setSelected(d.id) },
  })
  const retry = useMutation({ mutationFn: (id: number) => api.post<DocumentRec>(`/api/documents/${id}/extract`), onSuccess: () => qc.invalidateQueries({ queryKey: ['documents'] }) })
  const doc = docs.data?.find(d => d.id === selected)
  useEffect(() => {  // open the most recent document when none is selected
    if (selected === null && docs.data?.length) setSelected(docs.data[0].id)
  }, [docs.data, selected])
  const SAMPLES = ['invoice_new_room_over_limit.pdf', 'invoice_new_outpatient.pdf', 'invoice_missing_invoice_no.pdf',
    'discharge_summary_CLM-DEMO-01.pdf']

  return (
    <div className="space-y-6">
      <PageHeader index="04" section="Intake" title="Claim documents" desc={<>Upload a hospital bill or discharge summary. A Groq LLM extracts it into structured fields (text PDFs via the text model; images and scans via the vision model); you review and correct before it becomes a claim and passes through the same validation and detection.</>} />
      {meta && !meta.llm_enabled && (
        <div className="border-l-2 border-warn bg-warn-bg px-3 py-2 text-[12.5px] text-warn">Document extraction needs a Groq API key: add <code>GROQ_API_KEY=…</code> to <code>backend/.env</code> and restart the API. Everything else works without it.</div>
      )}
      <div className="grid gap-6 lg:grid-cols-[300px_minmax(0,1fr)]">
        <Card code="D.1" title="Upload" flush>
          <div className="space-y-2 p-3">
            <label className="flex h-8 cursor-pointer items-stretch border border-line bg-panel text-[12.5px] hover:border-ink">
              <span className="flex items-center border-r border-line bg-surface px-2.5 font-medium text-ink">Browse…</span>
              <span className={`flex min-w-0 flex-1 items-center truncate px-2 ${file ? 'num text-[12px] text-ink' : 'text-ink-3'}`}>{file ? file.name : 'PDF, PNG or JPG'}</span>
              <input className="sr-only" type="file" accept=".pdf,.png,.jpg,.jpeg" onChange={e => setFile(e.target.files?.[0] ?? null)} />
            </label>
            <Button variant="primary" disabled={!file || upload.isPending} onClick={() => upload.mutate()}>{upload.isPending ? 'Extracting…' : 'Upload & extract'}</Button>
            {upload.error && <p className="text-[12.5px] text-critical">{(upload.error as Error).message}</p>}
          </div>
          <div className="border-t border-line">
            <div className="eyebrow px-3 pb-1 pt-2.5">Sample documents</div>
            <ul className="pb-2">
              {SAMPLES.map(s => (
                <li key={s}><a className="num block truncate px-3 py-0.5 text-[11.5px] text-accent hover:underline" title={s} href={`/samples/documents/${s}`} download>↓ {s}</a></li>
              ))}
              <li className="px-3 py-0.5 text-[11.5px] text-ink-3">+ <span className="num">invoice_resubmitted_*.pdf</span> — an already-paid bill</li>
            </ul>
          </div>
          <div className="border-t border-line">
            <div className="eyebrow px-3 pb-1 pt-2.5">Uploaded{docs.data?.length ? ` · ${docs.data.length}` : ''}</div>
            <State loading={docs.isLoading} error={docs.error} empty={docs.data?.length === 0 && 'No documents yet.'}>
              <ul>
                {docs.data?.map(d => (
                  <li key={d.id}>
                    <button onClick={() => setSelected(d.id)} className={`grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-x-2 border-l-2 px-3 py-1.5 text-left hover:bg-surface ${selected === d.id ? 'border-ink bg-surface' : 'border-transparent'}`}>
                      <span className="num truncate text-[12px] text-ink">{d.filename}</span>
                      <Badge tone={d.status === 'FAILED' ? 'critical' : d.status === 'CONFIRMED' ? 'good' : 'info'}>{d.status.toLowerCase()}</Badge>
                      <span className="num col-span-2 text-[10.5px] text-ink-3">#{d.id} · {new Date(d.created_at).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}{d.claim_id ? ` · ${d.claim_id}` : ''}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </State>
          </div>
        </Card>
        <div className="min-w-0">
          {!doc && (
            <Card code="D.2" title="Review extraction">
              <ol className="max-w-xl space-y-1.5 text-[12.5px] text-ink-2">
                <li><span className="num mr-2 text-ink-3">1</span>Upload a bill or discharge summary, or download a sample on the left.</li>
                <li><span className="num mr-2 text-ink-3">2</span>Check the extracted header fields and invoice lines; fix anything misread.</li>
                <li><span className="num mr-2 text-ink-3">3</span>Create the claim — it runs through the same validation and leakage detection as file uploads.</li>
              </ol>
            </Card>
          )}
          {doc?.status === 'FAILED' && (
            <Card title={doc.filename}>
              <p className="text-[12.5px] text-critical">{doc.error}</p>
              <div className="mt-3"><Button disabled={retry.isPending} onClick={() => retry.mutate(doc.id)}>Retry extraction</Button></div>
            </Card>
          )}
          {doc?.extraction && <Review key={doc.id} doc={doc} />}
        </div>
      </div>
    </div>
  )
}
