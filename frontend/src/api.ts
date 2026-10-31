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
  status: 'OPEN' | 'CONFIRMED' | 'CORRECTED' | 'DISMISSED' | 'EVIDENCE_REQUESTED' | 'ESCALATED' | 'PENDING_APPROVAL'
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
  scan?: { id: number; status: ScanStatus; level: Level | null; score: number | null } | null
}
export type ScanStatus = 'QUEUED' | 'RUNNING' | 'DONE' | 'FAILED'
export type Level = 'LOW' | 'MEDIUM' | 'HIGH'
export interface ScanStep { tool: string; args: Record<string, unknown>; status: 'running' | 'done' | 'failed'; started: string; ms: number | null; summary: string | null }
export interface DuplicateMatch { page: number; fingerprint_id: number; doc_id: number | null; claim_id: string | null; label: string; source: string; ink_agreement: number; scale?: number; hash_distance: number }
export interface ScanView {
  id: number
  doc_id: number
  status: ScanStatus
  mode: string | null
  steps: ScanStep[]
  results: {
    inspect_document?: { pages: number; jpeg: boolean; raster_source: boolean; notes: string }
    find_duplicate_images?: { store_size: number; compared: number; duplicates: DuplicateMatch[]; same_template: DuplicateMatch[] }
    error_level_analysis?: { page: number; applicable: boolean; note: string; ghost_quality: number | null; hotspots: { x: number; y: number; w: number; h: number; z: number }[]; anomalous_fraction: number; heatmap_path: string | null }[]
    detect_copy_move?: { page: number; clusters: { offset: number[]; pairs: number }[]; structural_repeats: number }[]
  }
  score: number | null
  level: Level | null
  contributions: { skill: string; label: string; ran: boolean; score: number | null; detail: string }[]
  report: { summary: string; key_evidence: string[]; recommended_action: string; source: string; note?: string } | null
  error: string | null
  started_at: string
  finished_at: string | null
  progress: number
  stages: string[]
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

// ---- session token (memory first; sessionStorage so a reload keeps you signed in) ----
let token: string | null = (() => { try { return sessionStorage.getItem('token') } catch { return null } })()

export function getToken() { return token }
export function setToken(t: string | null) {
  token = t
  try { if (t) sessionStorage.setItem('token', t); else sessionStorage.removeItem('token') } catch { /* storage blocked */ }
  window.dispatchEvent(new Event('auth-changed'))
}
/** For <img src> and download links, which cannot send an Authorization header. */
export function authUrl(path: string) {
  return token ? `${path}${path.includes('?') ? '&' : '?'}access_token=${encodeURIComponent(token)}` : path
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(init?.headers || {}) },
  })
  if (res.status === 401 && token) setToken(null)  // expired → back to sign-in
  if (!res.ok) {
    let msg = res.statusText
    try { const j = await res.json(); msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail) } catch { /* keep status text */ }
    throw new Error(msg)
  }
  return res.json() as Promise<T>
}

export async function login(username: string, password: string) {
  const res = await fetch('/api/auth/token', { method: 'POST', body: new URLSearchParams({ username, password }) })
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Sign-in failed')
  setToken((await res.json()).access_token)
}

export interface Me {
  username: string
  full_name: string
  role: 'POLICYHOLDER' | 'HANDLER' | 'ANALYST' | 'SENIOR' | 'SIU' | 'ADMIN'
  role_label: string
  permissions: string[]
  authority_limit: number | null
  member_id: string | null
  policy_no: string | null
}

export const api = {
  get: <T,>(path: string) => request<T>(path),
  post: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body), headers: { 'Content-Type': 'application/json' } }),
  form: <T,>(path: string, fd: FormData) => request<T>(path, { method: 'POST', body: fd }),
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

export interface ClaimRow {
  claim_id: string
  claimant_name: string
  provider_id: string
  provider_name: string | null
  claim_type: string
  incident_date: string
  submitted_date: string
  source: string
  requested_amount: number
  proposed_settlement: number | null
  expected_settlement: number | null
  leakage_calculated: number
  leakage_estimated: number
  findings_confirmed: number
  findings_suspected: number
  findings_needs_info: number
  findings_open: number
  max_severity: 'HIGH' | 'MEDIUM' | 'LOW' | null
  review_status: string
  integrity: { doc_id: number; filename: string; level: Level | null; score: number | null; status: string | null } | null
  decision: { decision: string; approved_amount: number | null; user: string; created_at: string } | null
  state: string
  assigned_to: string | null
}
export interface ClaimsPage { total: number; counts: Record<string, number>; items: ClaimRow[] }

// ---- Level 2 investigation workspace ----
export interface Recommendation {
  id: number; finding_id: number; version: number; superseded: boolean; type: string; action: string
  target_line_ids: string[]; original_amount: number; corrected_amount: number; delta: number
  settlement_impact: number | null; calculation: string; rule_id: string; clause_ref: string | null
  clause_text: string | null; generated_by: string; created_at: string
}
export interface FindingDecisionRec {
  id: number; decision: string; reason: string | null; recommendation_id: number | null
  recommended_impact: number | null; override_impact: number | null; status: string; user: string; role: string
  approved_by: string | null; approval_reason: string | null; created_at: string
}
export type WsFinding = FindingRow & { recommendations: Recommendation[]; decisions: FindingDecisionRec[] }
export interface DocField { id: number; doc_id: number; doc_type: string; path: string; value: string; page: number | null; source_text: string | null; verified: boolean; method: string; model: string | null }
export interface WsDocument { id: number; filename: string; doc_type: string; source: string; content_type: string; status: string; fields: DocField[] }
export interface CompareRow { section: string; field: string; line_id?: string; claim_value: string | number | null; doc_value: string | number | null; status: string; source: { doc_id: number; doc_type: string; page?: number | null; source_text?: string | null; verified?: boolean } | null }
export interface RelatedClaim { claim_id: string; policy_no: string | null; claimant_name: string | null; provider_id: string | null; status: string | null; requested_amount: number | null; links: { rule: string; strength: string; attribute: string; value: string; explanation: string }[] }
export interface AuditRow { id: number; at: string; entity: string; entity_id: string | null; action: string; actor: string; actor_role: string; reason: string | null; before: Record<string, unknown> | null; after: Record<string, unknown> | null; ai_generated: boolean; model: string | null; hash: string }
export interface Workspace extends Omit<ClaimBundle, 'findings' | 'assessment'> {
  findings: WsFinding[]
  assessment: (NonNullable<ClaimBundle['assessment']> & { documents: CompareRow[] }) | null
  state: string; portal_label: string; assigned_to: string | null
  related: RelatedClaim[]
  documents: WsDocument[]
  evidence_requests: { id: number; message: string; doc_types: string[]; due_date: string; status: string; requested_by: string; created_at: string; responses: { id: number; responder: string; message: string | null; doc_ids: number[]; created_at: string }[] }[]
  escalations: { id: number; finding_id: number | null; target: string; reason: string; raised_by: string; status: string; outcome: string | null; resolution: string | null; resolved_by: string | null; created_at: string }[]
  notes: { id: number; author: string; role: string; visibility: string; text: string; created_at: string }[]
  rule_text: Record<string, string>
  timeline: AuditRow[]
  corrected_settlement: number | null
  open_findings: number
  decisions: { id: number; decision: string; approved_amount: number | null; reason: string; user: string; role: string; status: string; approved_by: string | null; created_at: string }[]
  can: { decide_findings: boolean; decide_claim: boolean; approve_clean: boolean; request_evidence: boolean; escalate: boolean; resolve_escalation: boolean; approve: boolean; note: boolean; siu_notes: boolean; hold: boolean; export: boolean; authority: number | null }
}
