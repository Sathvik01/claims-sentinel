import { useQuery } from '@tanstack/react-query'
import { api, inr, type Evaluation } from '../api'
import { Card, Kpi, PageHeader, State } from '../components/ui'

const pct = (x: number | null) => (x === null ? '—' : `${(x * 100).toFixed(1)}%`)

export default function Quality() {
  const q = useQuery({ queryKey: ['evaluation'], queryFn: () => api.get<Evaluation>('/api/evaluation') })
  const e = q.data
  return (
    <div className="space-y-6">
      <PageHeader index="07" section="Model assurance" title="Detection quality"
        desc={<>Findings scored against the synthetic dataset's ground truth: every planted leakage scenario, plus legitimate look-alikes that must <i>not</i> be flagged.</>} />
      <State loading={q.isLoading} error={q.error} empty={e && !e.available && (e.reason || 'Ground truth not available')}>
        {e?.available && (
          <>
            <div className="grid grid-cols-2 gap-px border-y border-line bg-line md:grid-cols-3 xl:grid-cols-6">
              <Kpi label="Claim-level recall" value={pct(e.recall)} sub={`${e.planted_claims} planted claims`} />
              <Kpi label="Claim-level precision" value={pct(e.precision)} sub={`${e.flagged_claims} flagged claims`} />
              <Kpi label="False flags on look-alikes" value={e.false_flags_on_legit} sub="recurring legitimate services" />
              <Kpi label="Flags outside ground truth" value={e.unexpected_flags} />
              <Kpi label="Expected calculated leakage" value={inr(e.expected_calculated_leakage)} />
              <Kpi label="Detected calculated leakage" value={inr(e.detected_calculated_leakage)} />
            </div>
            <Card code="Q.1" title="By scenario" flush>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[700px] text-[12.5px]">
                  <thead className="border-b border-line bg-surface text-left text-ink-2">
                    <tr><th className="px-3 py-2">Scenario</th><th className="px-3 py-2 text-right">Cases</th><th className="px-3 py-2 text-right">Correct</th>
                      <th className="px-3 py-2">Rate</th><th className="px-3 py-2 text-right">Amount exact (±₹1)</th><th className="px-3 py-2">Misses</th></tr>
                  </thead>
                  <tbody>
                    {e.scenarios.map(s => (
                      <tr key={s.scenario} className="border-b border-line">
                        <td className="px-3 py-1.5">{s.scenario.replace(/_/g, ' ')}<div className="text-xs text-ink-3">{s.metric}</div></td>
                        <td className="tabular px-3 py-1.5 text-right">{s.cases}</td>
                        <td className="tabular px-3 py-1.5 text-right">{s.detected}</td>
                        <td className="px-3 py-1.5">
                          <div className="flex items-center gap-2">
                            <div className="h-1.5 w-28 bg-surface"><div className="h-1.5 bg-s1" style={{ width: `${s.recall * 100}%` }} /></div>
                            <span className="tabular text-xs">{pct(s.recall)}</span>
                          </div>
                        </td>
                        <td className="tabular px-3 py-1.5 text-right">{s.amount_checked ? `${s.amount_exact}/${s.amount_checked}` : '—'}</td>
                        <td className="px-3 py-1.5 font-mono text-xs text-ink-3">{s.sample_misses.join(', ')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </>
        )}
      </State>
    </div>
  )
}
