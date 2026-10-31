# Handoff — Claims Sentinel (Level 2)

The approved plan is in [PLAN-LEVEL2.md](PLAN-LEVEL2.md); what was built and how to run it is in [README.md](README.md).
All phases (0–5) are implemented. `cd backend && uv run pytest` gives 27 passing tests. The ground-truth evaluation is
at 100% precision and recall.

## State
- **Done:**
  - Phase 0: integrity-scan progress and report UI, claims register, cache bug fix.
  - Phase 1: JWT login + RBAC, lifecycle, hash-chained audit, notifications.
  - Phase 2: correlation, document evidence with provenance, claim-vs-document comparison, typed recommendations,
    data scenarios 18–25.
  - Phase 3: investigation API and workspace UI, work queues, report PDF/JSON.
  - Phase 4: policyholder portal API and UI.
- **Verified in the browser:** the investigation workspace (findings/corrections and evidence tabs) and the portal
  home page at phone width.
- **Not yet verified in the browser:**
  - clicking through the decision buttons, escalation and approval prompts, the Related, Notes and Timeline tabs
  - the portal wizard and request-response UI

  (The same flows are covered by API tests: `tests/test_workflow.py`, `tests/test_portal.py`.)
- **Not live-tested with Groq** in Level 2: provenance verification of LLM-extracted values on portal uploads
  (`documents/evidence.fields_from_llm`).

## Conventions
- UI rules are in `CLAUDE.md` (no generic-AI look, ruled dense layouts, IBM Plex / JetBrains Mono / Source Serif).
- Use SDK features and package-manager versions only.
- The LLM (ChatGroq) never decides findings or amounts.
- Restart the API after backend changes (no `--reload`). A schema change needs the dev `claims.db` deleted, then
  **Load demo data**.
