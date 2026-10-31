import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type Batch, type BatchError } from '../api'
import { Badge, Button, Card, PageHeader, State, inputCls } from '../components/ui'

const SAMPLES = ['claims_bundle.xlsx', 'claims.csv', 'invoice_lines.json', 'policies.csv', 'policy_rules.csv', 'members.csv',
  'providers.csv', 'settlements.csv', 'claims_dirty.csv', 'ground_truth.csv']

export default function Ingestion() {
  const qc = useQueryClient()
  const [dataset, setDataset] = useState('bundle')
  const [file, setFile] = useState<File | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const datasets = useQuery({ queryKey: ['datasets'], queryFn: () => api.get<{ name: string; primary_key: string; fields: { name: string; required: boolean }[] }[]>('/api/ingest/datasets') })
  const batches = useQuery({ queryKey: ['batches'], queryFn: () => api.get<Batch[]>('/api/ingest/batches') })
  const errors = useQuery({ queryKey: ['batch-errors', selected], enabled: selected !== null, queryFn: () => api.get<BatchError[]>(`/api/ingest/batches/${selected}/errors`) })
  const refreshAll = () => qc.invalidateQueries()
  const upload = useMutation({
    mutationFn: () => api.upload<Batch[]>(`/api/ingest/${dataset}`, file!),
    onSuccess: bs => { refreshAll(); const bad = bs.find(b => b.rejected || b.warnings) ?? bs[0]; if (bad) setSelected(bad.id) },
  })
  const demo = useMutation({ mutationFn: () => api.post('/api/demo/load-sample'), onSuccess: () => { setSelected(null); refreshAll() } })
  const run = useMutation({ mutationFn: () => api.post<{ claims_analyzed: number; findings_count: number }>('/api/analysis/run'), onSuccess: refreshAll })
  const reset = useMutation({ mutationFn: () => api.post('/api/reset'), onSuccess: () => { setSelected(null); refreshAll() } })
  const schema = datasets.data?.find(d => d.name === dataset)

  return (
    <div className="space-y-6">
      <PageHeader index="05" section="Sources" title="Data ingestion"
        desc="Upload CSV, Excel or JSON. Every row is validated against its schema and referential links; rejected rows and warnings are itemised — nothing is dropped silently." />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card code="I.1" title="Upload a file">
          <div className="flex flex-wrap items-center gap-2">
            <select className={inputCls} value={dataset} onChange={e => setDataset(e.target.value)} aria-label="Dataset">
              <option value="bundle">Bundle (multi-sheet XLSX / JSON object)</option>
              {datasets.data?.map(d => <option key={d.name} value={d.name}>{d.name.replace('_', ' ')}</option>)}
            </select>
            <input className={`${inputCls} max-w-xs`} type="file" accept=".csv,.xlsx,.xls,.json" onChange={e => setFile(e.target.files?.[0] ?? null)} />
            <Button variant="primary" disabled={!file || upload.isPending} onClick={() => upload.mutate()}>{upload.isPending ? 'Validating…' : 'Upload & validate'}</Button>
          </div>
          {upload.error && <p className="mt-2 text-[12.5px] text-critical">{(upload.error as Error).message}</p>}
          {schema && (
            <p className="mt-3 text-xs text-ink-2">
              Columns: {schema.fields.map(f => <span key={f.name} className={f.required ? 'font-semibold text-ink' : ''}>{f.name}{f.required ? '*' : ''} </span>)}
              <span className="text-ink-3">(* required; dates YYYY-MM-DD or DD-MM-YYYY)</span>
            </p>
          )}
          {dataset === 'bundle' && <p className="mt-3 text-xs text-ink-2">One sheet / JSON key per dataset, loaded in dependency order: providers → policies → policy_rules → members → claims → invoice_lines → settlements.</p>}
          <div className="mt-4 border-b border-line pt-3 text-xs text-ink-2">
            Sample files: {SAMPLES.map(s => <a key={s} className="mr-3 text-accent" href={`/samples/${s}`} download>{s}</a>)}
            <div className="mt-1 text-ink-3">Try <b>claims_dirty.csv</b> as dataset “claims” to see row-level rejections.</div>
          </div>
        </Card>
        <Card code="I.2" title="Demo & analysis">
          <div className="space-y-3 text-[12.5px]">
            <div>
              <Button variant="primary" disabled={demo.isPending} onClick={() => demo.mutate()}>{demo.isPending ? 'Loading & analysing…' : 'Load demo data'}</Button>
              <p className="mt-1 text-xs text-ink-3">Resets the database and loads the synthetic dataset through the same validators.</p>
            </div>
            <div>
              <Button disabled={run.isPending} onClick={() => run.mutate()}>{run.isPending ? 'Running…' : 'Run analysis'}</Button>
              {run.data && <p className="mt-1 text-xs text-good">{run.data.claims_analyzed} pending claims analysed · {run.data.findings_count} findings</p>}
              <p className="mt-1 text-xs text-ink-3">Re-runs all detectors; reviewer decisions are preserved.</p>
            </div>
            <div><Button variant="danger" disabled={reset.isPending} onClick={() => { if (confirm('Delete all loaded data and findings?')) reset.mutate() }}>Clear database</Button></div>
            {(demo.error || run.error) && <p className="text-critical">{((demo.error || run.error) as Error).message}</p>}
          </div>
        </Card>
      </div>

      <Card code="I.3" title="Ingestion batches" flush>
        <State loading={batches.isLoading} error={batches.error} empty={batches.data?.length === 0 && 'No files loaded yet.'}>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[700px] text-[12.5px]">
              <thead className="border-b border-line bg-surface text-left text-ink-2">
                <tr><th className="px-3 py-2">#</th><th className="px-3 py-2">Dataset</th><th className="px-3 py-2">File</th>
                  <th className="px-3 py-2 text-right">Received</th><th className="px-3 py-2 text-right">Accepted</th><th className="px-3 py-2 text-right">Rejected</th><th className="px-3 py-2 text-right">Warnings</th><th className="px-3 py-2">Loaded</th></tr>
              </thead>
              <tbody>
                {batches.data?.map(b => (
                  <tr key={b.id} onClick={() => setSelected(b.id)} className={`cursor-pointer border-b border-line hover:bg-surface ${selected === b.id ? 'bg-info-bg' : ''}`}>
                    <td className="px-3 py-1.5 text-ink-3">{b.id}</td>
                    <td className="px-3 py-1.5">{b.dataset}</td>
                    <td className="px-3 py-1.5 text-ink-2">{b.filename} <span className="text-xs text-ink-3">({b.file_format})</span></td>
                    <td className="tabular px-3 py-1.5 text-right">{b.received.toLocaleString('en-IN')}</td>
                    <td className="tabular px-3 py-1.5 text-right text-good">{b.accepted.toLocaleString('en-IN')}</td>
                    <td className="tabular px-3 py-1.5 text-right">{b.rejected ? <Badge tone="critical">{b.rejected}</Badge> : 0}</td>
                    <td className="tabular px-3 py-1.5 text-right">{b.warnings ? <Badge tone="warn">{b.warnings}</Badge> : 0}</td>
                    <td className="px-3 py-1.5 text-xs text-ink-3">{new Date(b.created_at).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </State>
      </Card>

      {selected !== null && (
        <Card code="I.4" title={`Validation report · batch ${selected}`} flush>
          <State loading={errors.isLoading} error={errors.error} empty={errors.data?.length === 0 && 'All rows passed validation.'}>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[700px] text-[12.5px]">
                <thead className="border-b border-line bg-surface text-left text-ink-2">
                  <tr><th className="px-3 py-2">Row</th><th className="px-3 py-2">Record</th><th className="px-3 py-2">Field</th><th className="px-3 py-2">Value</th><th className="px-3 py-2">Problem</th><th className="px-3 py-2">Outcome</th></tr>
                </thead>
                <tbody>
                  {errors.data?.map(e => (
                    <tr key={e.id} className="border-b border-line">
                      <td className="tabular px-3 py-1.5 text-ink-3">{e.row_number ?? '—'}</td>
                      <td className="px-3 py-1.5 font-mono text-xs">{e.record_id ?? '—'}</td>
                      <td className="px-3 py-1.5">{e.field ?? '(row)'}</td>
                      <td className="max-w-[160px] truncate px-3 py-1.5 font-mono text-xs text-ink-2" title={e.value ?? ''}>{e.value ?? ''}</td>
                      <td className="px-3 py-1.5">{e.message}</td>
                      <td className="px-3 py-1.5">{e.severity === 'ERROR' ? <Badge tone="critical">✕ Rejected</Badge> : <Badge tone="warn">! Accepted with warning</Badge>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </State>
        </Card>
      )}
    </div>
  )
}
