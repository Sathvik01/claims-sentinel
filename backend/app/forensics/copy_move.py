"""Copy-move (clone) detection: keypoints that match other keypoints in the same page at one consistent,
spatially compact offset indicate a copied region (e.g. a duplicated stamp or signature).

Repetition along a single row or column (identical table cells, repeated words in a line) is expected in
bills, so axis-aligned offsets are reported separately as structural repetition, not as cloning."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image

RATIO = 0.5  # 2nd/3rd neighbour distance ratio
MIN_SHIFT = 40  # px; nearer matches are the same feature
AXIS_TOL = 32  # px; |dx| or |dy| below this = row/column repetition
OFFSET_BIN = 8
MIN_PAIRS = 35  # template text repeats give < 25 pairs on genuine bills
MAX_SPREAD = 260  # px; a cloned region is compact, scattered glyph matches are not


@dataclass
class CopyMoveResult:
    keypoints: int
    clusters: list[dict] = field(default_factory=list)  # {offset, pairs, source_box, target_box}
    structural_repeats: int = 0


def _box(pts: np.ndarray) -> dict:
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    return {"x": int(x0), "y": int(y0), "w": int(x1 - x0), "h": int(y1 - y0)}


def detect_copy_move(image: Image.Image) -> CopyMoveResult:
    gray = np.asarray(image.convert("L"))
    sift = cv2.SIFT_create(nfeatures=12000, contrastThreshold=0.02)
    kps, des = sift.detectAndCompute(gray, None)
    if des is None or len(kps) < 10:
        return CopyMoveResult(len(kps) if kps else 0)
    # g2NN: neighbour 0 is the point itself; keep neighbour 1 when clearly better than neighbour 2.
    matches = cv2.BFMatcher(cv2.NORM_L2).knnMatch(des, des, k=3)
    pts = np.array([k.pt for k in kps], dtype=np.float32)

    by_offset: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    seen: set[tuple[int, int]] = set()
    structural = 0
    for cand in matches:
        if len(cand) < 3:
            continue
        m, nxt = cand[1], cand[2]
        if m.distance > RATIO * nxt.distance:
            continue
        a, b = sorted((m.queryIdx, m.trainIdx))
        if a == b or (a, b) in seen:
            continue
        seen.add((a, b))
        d = pts[b] - pts[a]
        if np.hypot(*d) < MIN_SHIFT:
            continue
        if d[0] < 0 or (d[0] == 0 and d[1] < 0):  # canonical direction
            a, b, d = b, a, -d
        if abs(d[0]) < AXIS_TOL or abs(d[1]) < AXIS_TOL:
            structural += 1
            continue
        by_offset[(int(round(d[0] / OFFSET_BIN)), int(round(d[1] / OFFSET_BIN)))].append((a, b))

    # Merge neighbouring offset bins (rounding splits one clone across bins).
    merged: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    for key in sorted(by_offset, key=lambda k: -len(by_offset[k])):
        home = next((h for h in merged if abs(h[0] - key[0]) <= 1 and abs(h[1] - key[1]) <= 1), key)
        merged[home].extend(by_offset[key])

    clusters = []
    for key, pairs in merged.items():
        if len(pairs) < MIN_PAIRS:
            continue
        src = pts[[a for a, _ in pairs]]
        dst = pts[[b for _, b in pairs]]
        sb = _box(src)
        if max(sb["w"], sb["h"]) > MAX_SPREAD:
            continue
        clusters.append({"offset": [key[0] * OFFSET_BIN, key[1] * OFFSET_BIN], "pairs": len(pairs),
                         "source_box": sb, "target_box": _box(dst)})
    clusters.sort(key=lambda c: -c["pairs"])
    return CopyMoveResult(len(kps), clusters[:5], structural)
