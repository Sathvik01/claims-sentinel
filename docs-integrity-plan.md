# Claims Sentinel — Document Integrity Agent (image forensics skills + progress screen)

## Context
Claims Sentinel already extracts uploaded bills with ChatGroq and validates the resulting claims against
policy rules. It cannot tell whether the **document itself** is genuine. The user wants the validation agent
to have **skills (tools)** for image validation. Chosen scope:
- **LLM agent picks the tools.** A LangChain `create_agent` with ChatGroq tool calling decides which
  forensic skills to run on each document and writes the report. The **fabrication score is computed by
  code** from the tool outputs, so it stays reproducible and auditable.
- **Skills:** (1) duplicate / reused images and (2) tamper detection (Error Level Analysis plus copy-move
  clone detection).
- **Waiting screen:** a simple progress view (step indicator, progress bar and elapsed time), because a scan
  takes several seconds.

Results are labelled **suspected / needs review**, never "fraud", in line with the existing SUSPECTED_PATTERN
treatment.

## Architecture
```mermaid
flowchart LR
  UP[Upload PDF/JPG/PNG] --> EX[Extraction · ChatGroq (existing)]
  UP --> JOB[POST /documents/{id}/scan → DocumentScan row · BackgroundTasks]
  JOB --> AG[Integrity agent · langchain create_agent + ChatGroq]
  AG -->|tool calls| T1[inspect_document]
  AG --> T2[find_duplicate_images]
  AG --> T3[error_level_analysis]
  AG --> T4[detect_copy_move]
  AG --> T5[compute_fabrication_score · deterministic]
  MW[@wrap_tool_call middleware] -->|step start/end, timing, result| DB[(DocumentScan.steps)]
  AG -. structured ScanReport .-> DB
  UI[Progress screen polls GET /documents/{id}/scan every 1s] --> DB
  DB --> F[On claim confirm → Findings: DOCUMENT_TAMPER / DUPLICATE_DOCUMENT (SUSPECTED)]
```

## Backend

### Dependencies (install with `uv add`; no hand-pinned versions)
`langchain` (for `create_agent` and middleware), `pillow`, `imagehash`, `opencv-python-headless`, `numpy`,
and `pypdfium2` to render PDF pages to images. pypdfium2 has a permissive licence, unlike PyMuPDF's AGPL.

### New module `backend/app/forensics/`
- `render.py` — `page_images(doc) -> list[PIL.Image]`. Images load directly; PDFs are rendered per page with
  pypdfium2 at 150 dpi and capped at 3 pages. Results are cached under `uploads/pages/{doc_id}_{n}.png`.
- `duplicates.py` — fingerprints each page with `imagehash.phash` and `dhash`, plus an ORB descriptor set
  that catches crops. `find_matches(doc_id)` compares against every stored fingerprint, both uploaded
  documents and seeded historical ones. A pHash Hamming distance ≤ 4 is an exact match; ≤ 10 is a near match
  (re-saved or resized). If the hashes miss, an ORB match ratio above a threshold flags a crop. Each match
  returns the matched document or claim, the distance and the method.
- `ela.py` — re-saves the page as JPEG at quality 90, takes the absolute difference and amplifies it. Computes
  a robust z-score of block-level error (16 px blocks) and returns the share of anomalous area and up to 5
  hotspot boxes. It also saves a heatmap PNG for the UI. Text-born PDFs are flagged as `low_applicability`
  (ELA means little on rendered vectors), and the agent is told this in the tool description.
- `copy_move.py` — finds ORB keypoints within one page and matches them against each other while excluding
  near neighbours. It clusters match pairs by displacement vector (a consistent offset means a cloned
  region) and returns the clusters with their size and boxes.
- `score.py` — `fabrication_score(results) -> (0–100, level, contributions)`. Each skill produces a component
  score: duplicate exact 90, near 70, crop 60; ELA up to 60, scaled by anomalous area and peak z; copy-move up
  to 70, scaled by cluster size. They combine as `100·(1−Π(1−sᵢ/100))`. Levels are LOW < 25 ≤ MEDIUM < 60 ≤
  HIGH. A skill that wasn't run is shown as "not run", not counted as 0, and the report says which were skipped.

### Agent `backend/app/forensics/agent.py`
- `create_agent(model=ChatGroq(settings.groq_model…), tools=[...], system_prompt=…,
  response_format=ScanReport, middleware=[record_steps])`, invoked with `recursion_limit=12`. The `ChatGroq`
  instance is reused from `app/ai/llm.py`; `_chat()` is exposed for this.
- The tools are `@tool` wrappers around the forensic functions, taking `doc_id` and `page`. Their descriptions
  explain when each skill applies (for example, ELA is meaningful for photos and scans, not for digital PDFs).
  The prompt makes `inspect_document` the first call and `compute_fabrication_score` the last.
- The `record_steps` middleware (`@wrap_tool_call`) appends `{tool, args, status, started, ms, summary}` to
  `DocumentScan.steps` before and after each call, committing each time. This feeds the progress screen.
- `ScanReport` (Pydantic) holds `summary`, `key_evidence[]` and `recommended_action`. The score and level always
  come from `compute_fabrication_score`, and the same rule as the explainer applies: no new figures allowed.
- **Fallback:** with no `GROQ_API_KEY`, or if the agent errors, a fixed pipeline runs every skill in order
  and the report is a template. `mode` is recorded as "agent" or "fixed pipeline".

### Data & API
- New SQLModel tables in `app/models.py`:
  - `DocumentScan` — id, doc_id, status (QUEUED/RUNNING/DONE/FAILED), mode, steps JSON, progress, score,
    level, contributions JSON, report JSON, started/finished.
  - `ImageFingerprint` — doc_id or claim_id, page, phash, dhash, source (upload/seed).
- `app/api/documents.py`:
  - `POST /api/documents/{id}/scan` starts the job with FastAPI `BackgroundTasks`. The upload endpoint also
    starts it automatically after extraction.
  - `GET /api/documents/{id}/scan` returns the latest scan, with progress derived from completed steps over
    expected steps.
  - `GET /api/documents/{id}/pages/{n}` and `GET /api/scans/{scan_id}/ela/{n}` return the page image and the
    heatmap.
- **Findings link-up:** when `/confirm` creates a claim, a scan with level ≥ MEDIUM produces SUSPECTED findings.
  `DUPLICATE_DOCUMENT` is ESTIMATED at the proposed amount, with the matched claim as `related_claim_id`.
  `DOCUMENT_TAMPER` is NOT_DETERMINABLE, with the heatmap in its evidence. Both are added to `CATEGORY_LABELS`
  in `engine/base.py` and to the explainer `CHECKS`. The runner re-creates them on re-runs from the stored scan.

### Synthetic test documents (`app/synthetic/documents.py` + generator)
- **Genuine bills:** render invoice JPGs with Pillow (or via pypdfium2 from the existing PDFs).
- **Tampered:** the same bill with the total and one line amount pasted over from a patch saved at a different
  JPEG quality, which should produce an ELA hotspot.
- **Cloned:** a bill with a duplicated stamp or line region, for copy-move.
- **Reused:** a genuine historical bill resized 85% and re-saved, and another cropped by 10%, to test
  duplicate detection.
- **Seeded history:** `load-sample` fingerprints images of a few paid claims' bills as `source=seed`, so a
  reused document links to the claim it was originally paid on.

## Frontend (`pages/Documents.tsx` + a new `components/ScanProgress.tsx`)
- **Progress view:** shown in panel D.2 while a scan is QUEUED or RUNNING, polling every 1s with React Query
  `refetchInterval` until the scan finishes.
  - A horizontal step indicator with the stages Queued → Inspect → Duplicates → Tamper (ELA) → Clone check →
    Score → Report. Done stages are filled, the current one is labelled "running", and skipped ones are greyed
    with a "skipped by agent" note.
  - A thin square progress bar, the elapsed time in mono, and a one-line status from the latest step summary,
    using the existing ledger styling.
- **Report view** when the scan is done:
  - A fabrication meter (a ruled 0–100 bar with LOW/MEDIUM/HIGH bands), the score in mono and the level badge.
  - A per-skill contributions table showing skill, result, component score and time.
  - Duplicate matches shown side by side with page thumbnails, distance and the linked claim.
  - An ELA heatmap that can be toggled over the page image, with hotspot boxes.
  - The agent's summary and recommended action, plus a "Re-run scan" button.
- The upload list gets a small integrity column (level badge or "scanning…").
- The "Create claim" flow is unchanged, but a HIGH level shows a warning before confirming.

## Part B — Claims register & claim-level review (new screens)
Today the app lists **findings** (Alerts), and a claim is only reachable through one of its alerts. Clean
claims can't be browsed, and nothing records a decision on the claim as a whole.

**Backend**
- `GET /api/claims` returns a paginated, filterable register of every submission. Pending claims are the
  default; a filter shows paid history too. Each row carries:
  - Claim fields: id, claimant, provider, type, incident and submitted dates, source (synthetic / upload /
    document), requested, proposed and expected amounts.
  - Finding info: finding counts by type, calculated and estimated leakage, highest severity.
  - The document integrity level, if the claim came from a document.
  - The review status.
  - The filters reuse the existing `Filters` model in `app/api/findings.py`, plus `review_status` and `source`.
- **Review status** is derived, so it's always consistent:
  - `CLEAN` — no findings.
  - `NEEDS_INFO` — only needs-info findings.
  - `OPEN` — any open finding.
  - `REVIEWED` — every finding has been confirmed, corrected or dismissed.
  - A claim-level decision, once recorded, overrides these: `APPROVED`, `ADJUSTED`, `ON_HOLD` or `REJECTED`.
- New `ClaimDecision` table: claim_id, decision, approved amount, reason, user, role, timestamp. It forms an
  audit trail, with the latest row winning.
- `POST /api/claims/{id}/decision` is reviewer-only and requires a reason. `ADJUSTED` defaults the approved
  amount to the engine's expected settlement minus corrected or dismissed adjustments.
- The existing `GET /api/claims/{id}` bundle gains `decisions[]` and the document scan, if there is one.
- Decisions survive analysis re-runs, because they're keyed by claim_id.

**Frontend**
- New nav item **02 Claims**; the other items are renumbered.
- **Claims register page:**
  - The ledger-style header has counts by review status as clickable figures (Open, Needs info, Clean,
    Reviewed, Decided).
  - The FilterBar adds a review-status filter.
  - The dense table shows claim, claimant, provider, type, incident date, requested / proposed / expected
    amounts, leakage, findings (e.g. "3 ● 1 ◆"), highest severity, integrity level and status. It's sortable,
    with CSV export.
- **Claim review page** (`/claims/:id`), a single place to review the whole submission:
  - The header shows the claim, policy and amounts strip.
  - A findings checklist offers inline confirm / correct / dismiss on each finding, reusing the existing action
    endpoint.
  - The settlement recalculation and the "rules evaluated" panels are moved into shared components so the
    alert detail page reuses them.
  - Also: invoice lines, the document integrity report if any, and a **claim decision panel** (approve at the
    expected amount, adjust, hold or reject, each with a reason) with its audit trail.
- **Linking:**
  - The claim ID on Alerts and alert detail links to the claim page.
  - Overview ledger rows link to the register.
  - After confirming a document, the Documents page links to the new claim.

## Verification
- `uv run pytest`, adding `tests/test_forensics.py`:
  - ELA score: tampered > clean, with the hotspot overlapping the pasted patch.
  - Copy-move: finds the cloned stamp, with no clusters on a clean bill.
  - Duplicates: the resized/re-saved copy is an exact or near match, the crop is caught by ORB, and different
    bills don't match.
  - Score combination, and its levels with skills not run.
  - The fixed-pipeline fallback when no key is set.
- Live agent run with Groq: upload the tampered, reused and clean samples. Check that the agent's tool-call
  sequence is recorded in `steps`, scores and levels are sensible (clean LOW, tampered/reused ≥ MEDIUM), and the
  run time is acceptable.
- In the browser pane: watch the progress screen advance through the steps, then check the report (meter,
  heatmap toggle, duplicate side-by-side). Confirm a reused bill as a claim and see the DUPLICATE_DOCUMENT
  finding on the Alerts page linked to the original claim.
- Existing tests still pass, and ground-truth recall/precision is unchanged (the new findings apply only to
  uploaded documents).
- Claims register:
  - API tests check that the status counts add up to the total pending claims and that the hero claim shows as
    OPEN with 4 findings.
  - After all its findings are decided, a claim shows REVIEWED.
  - A decision requires a reason and the reviewer role, and survives `analysis/run`.
  - In the browser: filter the register by status, open a claim, act on its findings inline, approve it, then
    check the status change and audit trail.
