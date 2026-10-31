export type FindingType = 'CONFIRMED_VIOLATION' | 'SUSPECTED_PATTERN' | 'NEEDS_INFO'
export type AmountBasis = 'CALCULATED' | 'ESTIMATED' | 'NOT_DETERMINABLE'

export interface FindingRow {
  id: number
  claim_id: string
  category: string
  category_label: string
  rule_id: string
  clause_ref: string | null
  title: string
  finding_type: FindingType
  amount_basis: AmountBasis
  billed_excess: number | null
  leakage_amount: number | null
  effective_amount: number
  calculation: string
  line_ids: string[]
  related_claim_id: string | null
  severity: 'HIGH' | 'MEDIUM' | 'LOW'
  confidence: number
  status: 'OPEN' | 'CONFIRMED' | 'CORRECTED' | 'DISMISSED'
  corrected_amount: number | null
  claim_type?: string
  provider_id?: string
  provider_name?: string
  incident_date?: string
  claimant_name?: string
  requested_amount?: number
  evidence?: Record<string, unknown>
  ai_explanation?: Explanation | null
}

export interface Explanation {
  summary: string
  why_flagged: string
  what_to_check: string[]
  clarification_request?: string | null
  source: 'llm' | 'template'
  note?: string
}

export interface Claim {
  claim_id: string
  policy_no: string
  member_id: string
  claimant_name: string
  claim_type: string
  incident_date: string
  admission_date: string | null
  discharge_date: string | null
  diagnosis_code: string | null
  provider_id: string
  invoice_no: string | null
  requested_amount: number
  proposed_settlement: number | null
  submitted_date: string
  status: string
}

export interface Line {
  line_id: string
  service_date: string
  service_code: string
  description: string
  category: string
  quantity: number
  unit_price: number
  amount: number
}

export interface Check { rule_id: string; name: string; outcome: string; detail: string; clause_ref: string | null }
export interface WaterfallStep { label: string; amount: number; kind?: string; rule_id?: string; key?: string }

export interface ClaimBundle {
  claim: Claim
  lines: Line[]
  policy: Record<string, string | number> | null
  provider: { provider_id: string; name: string; city: string; tier: number } | null
  settlements: { paid_amount: number; paid_date: string }[]
  assessment: {
    billed_amount: number
    expected_settlement: number | null
    proposed_settlement: number | null
    gap: number | null
    reconciliation: string | null
    waterfall: WaterfallStep[]
    checks: Check[]
    cleared: { check: string; detail: string; related_claim_id?: string }[]
  } | null
  findings: FindingRow[]
}

export interface Action { id: number; user: string; role: string; action: string; reason: string | null; old_amount: number | null; new_amount: number | null; created_at: string }

export interface FindingDetail { finding: FindingRow; claim: ClaimBundle; related: ClaimBundle | null; actions: Action[] }

export interface Summary {
  claims_processed: number
  claims_flagged: number
  flagged_pct: number
  findings: number
  calculated_leakage: number
  estimated_leakage: number
  needs_info: number
  billed_amount: number
  proposed_amount: number
  by_category: { category: string; label: string; count: number; calculated: number; estimated: number }[]
  by_severity: Record<string, number>
  by_status: Record<string, number>
  by_finding_type: Record<string, number>
  top_providers: { provider_id: string; name: string; count: number; calculated: number; estimated: number }[]
}

export interface Meta {
  llm_enabled: boolean
  llm_provider: string
  llm_model: string
  llm_vision_model: string
  claim_types: string[]
  providers: { provider_id: string; name: string }[]
  categories: { value: string; label: string }[]
  severities: string[]
  statuses: string[]
  finding_types: string[]
}

export interface Batch { id: number; dataset: string; filename: string; file_format: string; created_at: string; received: number; accepted: number; rejected: number; warnings: number }
export interface BatchError { id: number; row_number: number | null; record_id: string | null; field: string | null; value: string | null; message: string; severity: 'ERROR' | 'WARNING' }

export interface DocumentRec {
  id: number
  filename: string
  status: 'UPLOADED' | 'EXTRACTED' | 'FAILED' | 'CONFIRMED'
  extraction: Extraction | null
  error: string | null
  claim_id: string | null
  created_at: string
}
export interface ExtractedLine { service_code: string | null; description: string; service_date: string | null; quantity: number; unit_price: number; amount: number; category: string }
export interface Extraction {
  document_type: string
  provider_id: string | null
  provider_name: string | null
  invoice_no: string | null
  invoice_date: string | null
  patient_name: string | null
  member_id: string | null
  policy_no: string | null
  claim_type: string | null
  admission_date: string | null
  discharge_date: string | null
  diagnosis_code: string | null
  diagnosis_text: string | null
  total_amount: number | null
  lines: ExtractedLine[]
  missing_fields: string[]
  notes: string | null
}

export interface Evaluation {
  available: boolean
  reason?: string
  scenarios: { scenario: string; cases: number; detected: number; recall: number; amount_checked: number; amount_exact: number; sample_misses: string[]; metric: string }[]
  planted_claims: number
  flagged_claims: number
  precision: number | null
  recall: number | null
  false_flags_on_legit: number
  unexpected_flags: number
  expected_calculated_leakage: number
  detected_calculated_leakage: number
}

export function getRole(): string {
  try { return localStorage.getItem('role') || 'reviewer' } catch { return 'reviewer' }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { 'X-Role': getRole(), 'X-User': `${getRole()}@demo`, ...(init?.headers || {}) },
  })
  if (!res.ok) {
    let msg = res.statusText
    try { const j = await res.json(); msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail) } catch { /* keep status text */ }
    throw new Error(msg)
  }
  return res.json() as Promise<T>
}

export const api = {
  get: <T,>(path: string) => request<T>(path),
  post: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body), headers: { 'Content-Type': 'application/json' } }),
  upload: <T,>(path: string, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return request<T>(path, { method: 'POST', body: fd })
  },
}

export const inr = (x: number | null | undefined, digits = 0) =>
  x === null || x === undefined ? '—'
    : (x < 0 ? '−₹' : '₹') + Math.abs(x).toLocaleString('en-IN', { maximumFractionDigits: digits, minimumFractionDigits: 0 })

export const inrShort = (x: number) =>
  x >= 1e7 ? `₹${(x / 1e7).toFixed(2)} Cr` : x >= 1e5 ? `₹${(x / 1e5).toFixed(2)} L` : inr(x)
