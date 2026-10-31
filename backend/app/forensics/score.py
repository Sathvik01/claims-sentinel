"""Fabrication score: deterministic combination of skill results (the agent never sets it)."""

from __future__ import annotations

LEVELS = [(60, "HIGH"), (25, "MEDIUM"), (0, "LOW")]
SKILLS = {
    "find_duplicate_images": "Duplicate / reused image",
    "error_level_analysis": "Edited regions (error level / JPEG ghost)",
    "detect_copy_move": "Cloned regions (copy-move)",
}


def _duplicate(r: dict) -> tuple[float, str]:
    dups = r.get("duplicates", [])
    if not dups:
        return 0.0, "no earlier document matches"
    best = dups[0]  # ordered by the skill: matches with a paid claim first
    exact = best["ink_agreement"] >= 0.995 and abs(best.get("scale", 1) - 1) < 0.02
    where = best.get("claim_id") or f"document #{best.get('doc_id')}"
    return (90.0 if exact else 75.0), (f"{'exact' if exact else 'resized/cropped'} copy of {where} "
                                       f"(ink agreement {best['ink_agreement']:.3f})")


def _ela(pages: list[dict]) -> tuple[float, str]:
    applicable = [p for p in pages if p.get("applicable")]
    if not applicable:
        return 0.0, pages[0].get("note", "not applicable") if pages else "not applicable"
    worst = max(applicable, key=lambda p: len(p.get("hotspots", [])))
    hs = worst.get("hotspots", [])
    if not hs:
        return 0.0, "no regions with inconsistent compression"
    s = min(70.0, 35 + 10 * len(hs) + min(15.0, 1000 * worst.get("anomalous_fraction", 0)))
    return s, f"{len(hs)} region(s) saved at a different time than the rest of page {worst['page'] + 1}"


def _clone(pages: list[dict]) -> tuple[float, str]:
    clusters = [(p["page"], c) for p in pages for c in p.get("clusters", [])]
    if not clusters:
        return 0.0, "no cloned regions"
    page, c = max(clusters, key=lambda pc: pc[1]["pairs"])
    return min(70.0, 40 + c["pairs"] / 5), (f"region on page {page + 1} copied with offset {c['offset']} "
                                            f"({c['pairs']} matching keypoints)")


def fabrication_score(results: dict) -> dict:
    contributions = []
    remaining = 1.0
    for skill, label in SKILLS.items():
        if skill not in results:
            contributions.append({"skill": skill, "label": label, "ran": False, "score": None,
                                  "detail": "not run by the agent"})
            continue
        r = results[skill]
        s, detail = (_duplicate(r) if skill == "find_duplicate_images"
                     else _ela(r) if skill == "error_level_analysis" else _clone(r))
        contributions.append({"skill": skill, "label": label, "ran": True, "score": round(s, 1), "detail": detail})
        remaining *= 1 - s / 100
    score = round(100 * (1 - remaining), 1)
    level = next(name for floor, name in LEVELS if score >= floor)
    return {"score": score, "level": level, "contributions": contributions,
            "skipped": [c["skill"] for c in contributions if not c["ran"]]}
