import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, authUrl, type Level, type ScanView } from '../api'
import { Badge, Button, Card, type Tone } from './ui'

const STAGE_LABEL: Record<string, string> = {
  inspect_document: 'Inspect',
  find_duplicate_images: 'Duplicates',
  error_level_analysis: 'Tamper (ELA)',
  detect_copy_move: 'Clone check',
  compute_fabrication_score: 'Score',
  report: 'Report',
}
const LEVEL_TONE: Record<Level, Tone> = { LOW: 'good', MEDIUM: 'serious', HIGH: 'critical' }

export function LevelBadge({ level }: { level: Level | null | undefined }) {
  if (!level) return null
  return <Badge tone={LEVEL_TONE[level]}>{level === 'HIGH' ? '▲' : level === 'MEDIUM' ? '■' : '▽'} {level.toLowerCase()} risk</Badge>
}

function elapsed(from: string, to?: string | null) {
  const s = Math.max(0, ((to ? new Date(to) : new Date()).getTime() - new Date(from).getTime()) / 1000)
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`
}

/** Waiting screen: stage rail, progress bar, elapsed time, latest step summary. */
function Progress({ scan }: { scan: ScanView }) {
  const [, tick] = useState(0)
  useEffect(() => { const t = setInterval(() => tick(x => x + 1), 1000); return () => clearInterval(t) }, [])
  const byTool = new Map(scan.steps.map(s => [s.tool, s]))
  const running = scan.steps.findLast(s => s.status === 'running')
  const latest = scan.steps.findLast(s => s.summary)
  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between gap-3">
        <div>
          <div className="eyebrow">Integrity agent · {scan.status === 'QUEUED' ? 'queued' : 'working'}</div>
          <div className="mt-1 font-serif text-[20px]">Validating document…</div>
        </div>
        <div className="num text-[22px] text-ink">{elapsed(scan.started_at)}</div>
      </div>
      <ol className="grid grid-cols-3 gap-px border border-line bg-line sm:grid-cols-6">
        {scan.stages.map((stage, i) => {
          const st = byTool.get(stage)
          const state = st?.status === 'done' ? 'done' : st?.status === 'running' ? 'running' : 'waiting'
          return (
            <li key={stage} className={`px-2.5 py-2 ${state === 'done' ? 'bg-ink text-panel' : state === 'running' ? 'bg-info-bg' : 'bg-panel'}`}>
              <div className={`num text-[10px] ${state === 'done' ? 'text-panel/70' : 'text-ink-3'}`}>{String(i + 1).padStart(2, '0')}</div>
              <div className={`text-[12px] font-medium ${state === 'waiting' ? 'text-ink-3' : ''}`}>{STAGE_LABEL[stage] ?? stage}</div>
              <div className={`num text-[10px] ${state === 'running' ? 'text-accent' : state === 'done' ? 'text-panel/70' : 'text-ink-3'}`}>
                {state === 'running' ? 'running…' : state === 'done' ? `${((st?.ms ?? 0) / 1000).toFixed(1)}s` : '—'}
              </div>
            </li>
          )
        })}
      </ol>
      <div className="h-1.5 w-full bg-surface"><div className="h-1.5 bg-ink transition-all duration-500" style={{ width: `${Math.max(4, scan.progress * 100)}%` }} /></div>
      <div className="flex items-center justify-between gap-3 text-[12.5px] text-ink-2">
        <span>{running ? <>Running <span className="num">{running.tool}</span>{Object.keys(running.args).length ? <span className="num text-ink-3"> {JSON.stringify(running.args)}</span> : null}</> : 'Waiting for the agent to choose a skill…'}</span>
        <span className="num text-ink-3">{Math.round(scan.progress * 100)}%</span>
      </div>
      {latest?.summary && <div className="border-l-2 border-ink bg-surface px-3 py-2 text-[12.5px]"><span className="num text-ink-3">{latest.tool}</span> → {latest.summary}</div>}
      <p className="text-[11.5px] text-ink-3">The agent decides which forensic skills apply to this document. Typical run: 15–35 seconds.</p>
    </div>
  )
}

function Meter({ score, level }: { score: number; level: Level }) {
  return (
    <div>
      <div className="relative h-3 border border-line">
        <div className="absolute inset-y-0 left-0 w-1/4 bg-good-bg" /><div className="absolute inset-y-0 left-1/4 w-[35%] bg-serious-bg" /><div className="absolute inset-y-0 left-[60%] right-0 bg-critical-bg" />
        <div className="absolute -top-1 h-5 w-0.5 bg-ink" style={{ left: `calc(${score}% - 1px)` }} />
      </div>
      <div className="num mt-1 flex justify-between text-[10px] text-ink-3"><span>0 low</span><span className="ml-[17%]">25 medium</span><span className="ml-[22%]">60 high</span><span>100</span></div>
      <div className="mt-2 flex items-center gap-2"><span className="num text-[28px] font-medium leading-none">{score.toFixed(1)}</span><LevelBadge level={level} /></div>
    </div>
  )
}

function Report({ scan, docId, onRerun, rerunning }: { scan: ScanView; docId: number; onRerun: () => void; rerunning: boolean }) {
  const [heat, setHeat] = useState(true)
  const dups = scan.results.find_duplicate_images?.duplicates ?? []
  const ela = (scan.results.error_level_analysis ?? []).find(p => p.applicable && p.heatmap_path)
  return (
    <div className="space-y-5">
      <div className="grid gap-5 lg:grid-cols-[260px_minmax(0,1fr)]">
        <div>
          <div className="eyebrow mb-2">Fabrication score</div>
          {scan.score !== null && scan.level && <Meter score={scan.score} level={scan.level} />}
          <div className="num mt-3 text-[11px] text-ink-3">mode: {scan.mode} · {scan.finished_at ? elapsed(scan.started_at, scan.finished_at) : ''}</div>
        </div>
        <div>
          <div className="eyebrow mb-1">Agent report</div>
          {scan.report && <>
            <p className="text-[13px] leading-relaxed">{scan.report.summary}</p>
            <ul className="mt-2 list-disc space-y-0.5 pl-5 text-[12.5px] text-ink-2">{scan.report.key_evidence.map(e => <li key={e}>{e}</li>)}</ul>
            <p className="mt-2 border-l-2 border-ink pl-2 text-[12.5px] font-medium">{scan.report.recommended_action}</p>
            <p className="num mt-1 text-[10.5px] text-ink-3">written by {scan.report.source}{scan.report.note ? ` — ${scan.report.note}` : ''}</p>
          </>}
        </div>
      </div>

      <table className="w-full text-[12.5px]">
        <thead className="border-b border-line bg-surface text-left text-ink-2">
          <tr><th className="px-2 py-1.5">Skill</th><th className="px-2 py-1.5">Result</th><th className="px-2 py-1.5 text-right">Score</th><th className="px-2 py-1.5 text-right">Time</th></tr>
        </thead>
        <tbody>
          {scan.contributions.map(c => {
            const step = scan.steps.find(s => s.tool === c.skill)
            return (
              <tr key={c.skill} className={`border-b border-line ${c.ran ? '' : 'text-ink-3'}`}>
                <td className="px-2 py-1.5">{c.label}</td>
                <td className="px-2 py-1.5">{c.ran ? c.detail : 'skipped by agent'}</td>
                <td className="num px-2 py-1.5 text-right">{c.score === null ? '—' : c.score.toFixed(0)}</td>
                <td className="num px-2 py-1.5 text-right">{step?.ms != null ? `${(step.ms / 1000).toFixed(1)}s` : '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>

      {dups.length > 0 && (
        <div>
          <div className="eyebrow mb-2">Matched earlier document</div>
          <div className="grid grid-cols-2 gap-px border border-line bg-line">
            <figure className="bg-panel p-2">
              <img className="w-full border border-line" src={authUrl(`/api/documents/${docId}/pages/${dups[0].page}`)} alt="Submitted page" />
              <figcaption className="num mt-1 text-[11px] text-ink-2">this upload · page {dups[0].page + 1}</figcaption>
            </figure>
            <figure className="bg-panel p-2">
              <img className="w-full border border-line" src={authUrl(`/api/fingerprints/${dups[0].fingerprint_id}/image`)} alt="Matched document" />
              <figcaption className="num mt-1 text-[11px] text-ink-2">{dups[0].claim_id ?? `document #${dups[0].doc_id}`} · ink agreement {dups[0].ink_agreement.toFixed(3)}{dups[0].scale ? ` · scale ${dups[0].scale.toFixed(2)}` : ''}</figcaption>
            </figure>
          </div>
        </div>
      )}

      {ela && (
        <div>
          <div className="mb-2 flex items-center justify-between">
            <span className="eyebrow">Page {ela.page + 1} · {ela.hotspots.length} suspect region(s)</span>
            <Button variant="ghost" onClick={() => setHeat(h => !h)}>{heat ? 'Show original' : 'Show heatmap'}</Button>
          </div>
          <img className="w-full border border-line" alt={heat ? 'Error-level heatmap' : 'Original page'}
            src={authUrl(heat ? `/api/scans/${scan.id}/ela/${ela.page}` : `/api/documents/${docId}/pages/${ela.page}`)} />
          <p className="mt-1 text-[11.5px] text-ink-3">{ela.note}. Boxed regions lack the JPEG ghost the rest of the page shows — they were saved later.</p>
        </div>
      )}
      <div><Button disabled={rerunning} onClick={onRerun}>{rerunning ? 'Starting…' : 'Re-run scan'}</Button></div>
    </div>
  )
}

export default function ScanPanel({ docId }: { docId: number }) {
  const qc = useQueryClient()
  const q = useQuery({
    queryKey: ['scan', docId],
    queryFn: () => api.get<ScanView | null>(`/api/documents/${docId}/scan`),
    refetchInterval: query => (query.state.data && ['QUEUED', 'RUNNING'].includes(query.state.data.status) ? 1000 : false),
  })
  const rerun = useMutation({ mutationFn: () => api.post(`/api/documents/${docId}/scan`), onSuccess: () => qc.invalidateQueries({ queryKey: ['scan', docId] }) })
  const scan = q.data
  useEffect(() => { if (scan?.status === 'DONE') qc.invalidateQueries({ queryKey: ['documents'] }) }, [scan?.status, qc])
  return (
    <Card code="D.3" title="Document integrity" actions={scan?.status === 'DONE' && <LevelBadge level={scan.level} />}>
      {!scan ? <p className="text-[12.5px] text-ink-3">No integrity scan yet.</p>
        : scan.status === 'FAILED' ? <p className="text-[12.5px] text-critical">Scan failed: {scan.error}</p>
        : scan.status === 'DONE' ? <Report scan={scan} docId={docId} onRerun={() => rerun.mutate()} rerunning={rerun.isPending} />
        : <Progress scan={scan} />}
    </Card>
  )
}
