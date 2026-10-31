"""Document-integrity agent: a LangChain agent (ChatGroq tool calling) chooses which forensic skills to run.
Every tool call is recorded as a step on the DocumentScan row, which drives the progress screen.
The fabrication score always comes from `compute_fabrication_score` (code), never from the model."""

from __future__ import annotations

import json
import time
import traceback
from datetime import UTC, datetime

from langchain.agents import create_agent
from langchain.agents.middleware import wrap_tool_call
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session

from app.ai import llm
from app.ai.explainer import _numbers
from app.config import settings
from app.db import engine
from app.forensics import service
from app.forensics.score import fabrication_score
from app.models import Document, DocumentScan

STAGES = ["inspect_document", "find_duplicate_images", "error_level_analysis", "detect_copy_move",
          "compute_fabrication_score", "report"]

SYSTEM = """You are the document-integrity skill of an insurance claims validation agent.
You receive one uploaded claim document (hospital bill, discharge summary, photo or scan) and decide which
forensic skills to run. Rules:
1. Call inspect_document first.
2. Always call find_duplicate_images (bills are often resubmitted from earlier claims).
3. Call error_level_analysis only for JPEG photos/scans (inspect_document tells you); skip it for digital PDFs.
4. Call detect_copy_move on each page that could carry stamps, signatures or amounts.
5. Call compute_fabrication_score last; never estimate the score yourself.
Then write the report: plain, factual, using only figures returned by the tools. Findings are suspicions
for a human reviewer, never a fraud determination."""


class ScanReport(BaseModel):
    summary: str = Field(description="2-3 sentences: what was checked and what was found")
    key_evidence: list[str] = Field(description="Up to 4 concrete observations from the tool results")
    recommended_action: str = Field(description="One sentence: what the claims reviewer should do next")


class ScanContext:
    def __init__(self, scan_id: int, doc: Document):
        self.scan_id = scan_id
        self.dp = service.DocumentPages(doc)
        self.results: dict = {}

    # -- step log (fresh session per write: the agent runs in a worker thread) --
    def _update(self, fn) -> None:
        with Session(engine) as s:
            scan = s.get(DocumentScan, self.scan_id)
            fn(scan)
            flag_modified(scan, "steps")  # JSON column mutated in place
            scan.results = json.loads(json.dumps(self.results, default=str))
            s.add(scan)
            s.commit()

    def step_start(self, tool_name: str, args: dict) -> int:
        idx = {}

        def f(scan):
            scan.steps.append({"tool": tool_name, "args": args, "status": "running",
                               "started": datetime.now(UTC).isoformat(), "ms": None, "summary": None})
            idx["i"] = len(scan.steps) - 1
        self._update(f)
        return idx["i"]

    def step_end(self, i: int, summary: str, ms: int, status: str = "done") -> None:
        def f(scan):
            scan.steps[i] = scan.steps[i] | {"status": status, "summary": summary, "ms": ms}
        self._update(f)

    # -- skills --
    def inspect(self) -> dict:
        r = self.dp.inspect()
        self.results["inspect_document"] = r
        return r

    def duplicates(self) -> dict:
        with Session(engine) as s:
            r = service.find_duplicates(s, self.dp)
        self.results["find_duplicate_images"] = r
        return r

    def ela(self, page: int) -> dict:
        r = service.run_ela(self.dp, self.scan_id, page)
        self.results.setdefault("error_level_analysis", [])
        self.results["error_level_analysis"] = [x for x in self.results["error_level_analysis"] if x["page"] != page] + [r]
        return r

    def copy_move(self, page: int) -> dict:
        r = service.run_copy_move(self.dp, page)
        self.results.setdefault("detect_copy_move", [])
        self.results["detect_copy_move"] = [x for x in self.results["detect_copy_move"] if x["page"] != page] + [r]
        return r

    def score(self) -> dict:
        r = fabrication_score({k: v for k, v in self.results.items() if k != "inspect_document"})
        self.results["compute_fabrication_score"] = r
        return r


def _summaries(name: str, r: dict) -> str:
    if name == "inspect_document":
        return f"{r['pages']} page(s), {'JPEG scan/photo' if r['jpeg'] else 'digital PDF' if not r['raster_source'] else 'lossless image'}"
    if name == "find_duplicate_images":
        d = r["duplicates"]
        return (f"matches {d[0].get('claim_id') or 'document #' + str(d[0].get('doc_id'))} "
                f"(ink agreement {d[0]['ink_agreement']:.3f})" if d
                else f"no reuse among {r['store_size']} stored pages ({len(r['same_template'])} same-template look-alikes)")
    if name == "error_level_analysis":
        return (f"page {r['page'] + 1}: {len(r['hotspots'])} suspect region(s)" if r["applicable"]
                else f"page {r['page'] + 1}: not applicable — {r['note']}")
    if name == "detect_copy_move":
        return (f"page {r['page'] + 1}: cloned region, {r['clusters'][0]['pairs']} keypoint pairs" if r["clusters"]
                else f"page {r['page'] + 1}: no cloned regions")
    if name == "compute_fabrication_score":
        return f"score {r['score']} → {r['level']}"
    return ""


def _compact(name: str, r: dict) -> dict:
    """What the model sees: the facts, without image paths and bulky arrays."""
    if name == "find_duplicate_images":
        keep = ("page", "claim_id", "doc_id", "label", "ink_agreement", "scale", "source")
        return {"store_size": r["store_size"], "duplicates": [{k: d.get(k) for k in keep} for d in r["duplicates"]],
                "same_template_lookalikes": len(r["same_template"])}
    if name == "error_level_analysis":
        return {k: r[k] for k in ("page", "applicable", "note", "ghost_quality", "anomalous_fraction", "hotspots")}
    if name == "detect_copy_move":
        return {"page": r["page"], "clusters": r["clusters"], "structural_repeats_ignored": r["structural_repeats"]}
    return r


def _tools(ctx: ScanContext):
    @tool
    def inspect_document() -> dict:
        """Describe the document: page count, size, whether it is a JPEG photo/scan or a digital PDF, and which
        skills are meaningful for it. Always call this first."""
        return ctx.inspect()

    @tool
    def find_duplicate_images() -> dict:
        """Compare every page against all previously submitted and paid documents to find reused bills,
        including resized, re-saved or cropped copies. Different bills printed on the same hospital template
        are reported separately as look-alikes, not duplicates."""
        return _compact("find_duplicate_images", ctx.duplicates())

    @tool
    def error_level_analysis(page: int = 0) -> dict:
        """Find regions of a JPEG page that were saved at a different time than the rest (e.g. a retyped amount
        or pasted name), using JPEG ghost / error-level analysis. Only meaningful for JPEG photos and scans.

        Args:
            page: zero-based page index
        """
        return _compact("error_level_analysis", ctx.ela(page))

    @tool
    def detect_copy_move(page: int = 0) -> dict:
        """Detect regions copied within the same page (duplicated stamps, signatures or figures). Repetition
        along a table row or column is expected and ignored.

        Args:
            page: zero-based page index
        """
        return _compact("detect_copy_move", ctx.copy_move(page))

    @tool
    def compute_fabrication_score() -> dict:
        """Combine the results of the skills run so far into the fabrication score (0-100) and level
        (LOW / MEDIUM / HIGH). Call this last."""
        return ctx.score()

    return [inspect_document, find_duplicate_images, error_level_analysis, detect_copy_move, compute_fabrication_score]


def _record(ctx: ScanContext):
    @wrap_tool_call
    def record_steps(request, handler):
        name = request.tool_call["name"]
        if name not in STAGES:  # e.g. the structured-response tool
            return handler(request)
        i = ctx.step_start(name, request.tool_call.get("args") or {})
        t = time.perf_counter()
        try:
            out = handler(request)
        except Exception as e:
            ctx.step_end(i, f"failed: {e}", int((time.perf_counter() - t) * 1000), "failed")
            raise
        latest = ctx.results.get(name)
        if isinstance(latest, list):
            page = (request.tool_call.get("args") or {}).get("page", 0)
            latest = next((x for x in latest if x["page"] == page), latest[-1])
        ctx.step_end(i, _summaries(name, latest) if latest else "", int((time.perf_counter() - t) * 1000))
        return out
    return record_steps


def _run_fixed(ctx: ScanContext) -> None:
    """Deterministic plan; skills the agent already ran are not repeated."""
    plan = []
    if "inspect_document" not in ctx.results:
        plan.append(("inspect_document", {}, ctx.inspect))
    if "find_duplicate_images" not in ctx.results:
        plan.append(("find_duplicate_images", {}, ctx.duplicates))
    for p in range(len(ctx.dp.pages)):
        done = lambda k: any(x["page"] == p for x in ctx.results.get(k, []))  # noqa: E731
        if ctx.dp.pages[p].raster_source and not done("error_level_analysis"):
            plan.append(("error_level_analysis", {"page": p}, lambda p=p: ctx.ela(p)))
        if not done("detect_copy_move"):
            plan.append(("detect_copy_move", {"page": p}, lambda p=p: ctx.copy_move(p)))
    plan.append(("compute_fabrication_score", {}, ctx.score))
    for name, args, fn in plan:
        i = ctx.step_start(name, args)
        t = time.perf_counter()
        r = fn()
        ctx.step_end(i, _summaries(name, r), int((time.perf_counter() - t) * 1000))


REPORT_SYSTEM = (
    "You write the document-integrity report for a claims reviewer from forensic tool results (JSON). "
    "Use only facts and figures present in the JSON; do not compute new numbers. The fabrication score and "
    "level are final. Describe signals as suspicions needing review, never as fraud."
)


def _llm_report(ctx: ScanContext) -> tuple[dict | None, str | None]:
    facts = {k: (_compact(k, v) if not isinstance(v, list) else [_compact(k, x) for x in v])
             for k, v in ctx.results.items()}
    payload = json.dumps(facts, default=str, ensure_ascii=False)
    try:
        rep = llm.generate(ScanReport, payload, REPORT_SYSTEM).model_dump()
    except Exception as e:
        return None, f"report model unavailable ({type(e).__name__}); template report shown"
    text = " ".join([rep["summary"], *rep["key_evidence"], rep["recommended_action"]])
    extra = {n for n in _numbers(text) - _numbers(payload) if not (n.isdigit() and int(n) <= 10)}
    if extra:
        return None, f"report used figures not in the tool results ({', '.join(sorted(extra)[:4])}); template shown"
    return rep | {"source": "llm"}, None


def _template_report(score: dict) -> dict:
    hits = [c for c in score["contributions"] if c["ran"] and c["score"]]
    return {
        "summary": (f"Fabrication score {score['score']} ({score['level']}). "
                    + ("Signals: " + "; ".join(c["detail"] for c in hits) + "." if hits else "No integrity signals found.")),
        "key_evidence": [f"{c['label']}: {c['detail']}" for c in score["contributions"]],
        "recommended_action": ("Hold the claim and verify the original document with the provider."
                               if score["level"] == "HIGH" else
                               "Review the highlighted evidence before settlement." if score["level"] == "MEDIUM"
                               else "No document-integrity action needed."),
        "source": "template",
    }


def run_scan(scan_id: int) -> None:
    with Session(engine) as s:
        scan = s.get(DocumentScan, scan_id)
        doc = s.get(Document, scan.doc_id)
        scan.status, scan.started_at = "RUNNING", datetime.now(UTC)
        s.add(scan)
        s.commit()
        s.refresh(doc)
        s.expunge(doc)  # used outside this session by the worker
    ctx = ScanContext(scan_id, doc)
    mode, report, note = "fixed pipeline", None, None
    try:
        if llm.available():
            try:
                agent = create_agent(model=llm._chat(settings.groq_model), tools=_tools(ctx), system_prompt=SYSTEM,
                                     middleware=[_record(ctx)])
                agent.invoke({"messages": [{"role": "user", "content":
                              f"Validate document #{doc.id} '{doc.filename}' ({doc.content_type})."}]},
                             config={"recursion_limit": 16})
                mode = "agent"
            except Exception as e:  # tool-calling failure: finish only the skills the agent didn't run
                note = f"agent stopped early ({type(e).__name__}); remaining skills ran as a fixed pipeline"
                traceback.print_exc()
                mode = "agent + fixed pipeline" if ctx.results else "fixed pipeline"
                _run_fixed(ctx)
            if "compute_fabrication_score" not in ctx.results:
                ctx.score()
            i = ctx.step_start("report", {})
            t = time.perf_counter()
            report, rnote = _llm_report(ctx)
            note = note or rnote
            ctx.step_end(i, "written by the LLM" if report else "template", int((time.perf_counter() - t) * 1000))
        else:
            _run_fixed(ctx)
        score = ctx.results.get("compute_fabrication_score") or ctx.score()
        report = report or _template_report(score)
        if note:
            report["note"] = note

        def finish(scan):
            scan.status, scan.mode = "DONE", mode
            scan.score, scan.level, scan.contributions = score["score"], score["level"], score["contributions"]
            scan.report, scan.finished_at = report, datetime.now(UTC)
            if not any(st["tool"] == "report" for st in scan.steps):
                scan.steps.append({"tool": "report", "args": {}, "status": "done", "ms": 0,
                                   "started": datetime.now(UTC).isoformat(), "summary": "template"})
        ctx._update(finish)
    except Exception as e:
        traceback.print_exc()

        def fail(scan):
            scan.status, scan.error, scan.finished_at = "FAILED", f"{type(e).__name__}: {e}", datetime.now(UTC)
        ctx._update(fail)
