"""Duplicate / reused document images.

Stage 1: perceptual hashes (pHash, dHash) shortlist candidates cheaply and catch re-saved or resized copies.
Stage 2: SIFT + RANSAC homography aligns each candidate onto the page (handles crops and scale) and compares
the aligned ink. Bills printed from the same hospital template share layout but not their figures and
names, so ink overlap — not layout similarity — decides whether it is the same document."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import imagehash
import numpy as np
from PIL import Image

SHORTLIST = 8
MIN_INLIERS = 40
EXACT_HASH = 4  # pHash Hamming distance
SAME_DOC_INK = 0.97  # aligned ink agreement; different bills on one template measure <= 0.95


@dataclass
class Fingerprint:
    phash: str
    dhash: str


def fingerprint(img: Image.Image) -> Fingerprint:
    return Fingerprint(str(imagehash.phash(img)), str(imagehash.dhash(img)))


def hash_distance(a: str, b: str) -> int:
    return imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b)


WORK_SIDE = 1000  # match on a downscaled page: fast, and ink agreement is scale-tolerant
_FEATURES: dict[str, tuple] = {}


def _prep(img: Image.Image, key: str | None = None) -> tuple[np.ndarray, list, np.ndarray | None]:
    """Downscaled grayscale + SIFT features, cached per key (fingerprint image path)."""
    if key and key in _FEATURES:
        return _FEATURES[key]
    g = np.asarray(img.convert("L"))
    f = WORK_SIDE / max(g.shape)
    if f < 1:
        g = cv2.resize(g, (int(g.shape[1] * f), int(g.shape[0] * f)), interpolation=cv2.INTER_AREA)
    kps, des = cv2.SIFT_create(nfeatures=3000).detectAndCompute(g, None)
    out = (g, [k.pt for k in kps], des)
    if key:
        _FEATURES[key] = out
    return out


def compare(query: Image.Image, candidate: Image.Image, query_key: str | None = None,
            candidate_key: str | None = None) -> dict:
    """Align candidate onto query (SIFT + RANSAC homography) and measure aligned ink agreement (0-1)."""
    q, pq, dq = _prep(query, query_key)
    c, pc, dc = _prep(candidate, candidate_key)
    if dq is None or dc is None or len(pq) < 10 or len(pc) < 10:
        return {"inliers": 0, "ink_agreement": 0.0}
    good = [m for m, n in cv2.BFMatcher(cv2.NORM_L2).knnMatch(dc, dq, k=2) if m.distance < 0.75 * n.distance]
    if len(good) < MIN_INLIERS:
        return {"inliers": len(good), "ink_agreement": 0.0}
    src = np.float32([pc[m.queryIdx] for m in good])
    dst = np.float32([pq[m.trainIdx] for m in good])
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
    inliers = int(mask.sum()) if mask is not None else 0
    if H is None or inliers < MIN_INLIERS:
        return {"inliers": inliers, "ink_agreement": 0.0}
    size = (q.shape[1], q.shape[0])
    warped = cv2.warpPerspective(c, H, size, borderValue=255)
    valid = cv2.warpPerspective(np.full_like(c, 255), H, size, borderValue=0) > 0
    a, b = (q < 140) & valid, (warped < 140) & valid
    kernel = np.ones((3, 3), np.uint8)
    a_d, b_d = cv2.dilate(a.astype(np.uint8), kernel) > 0, cv2.dilate(b.astype(np.uint8), kernel) > 0
    # Share of each page's ink that lands on the other's ink (symmetric, 1px tolerance).
    agree = min((a & b_d).sum() / max(a.sum(), 1), (b & a_d).sum() / max(b.sum(), 1))
    scale = float(np.sqrt(abs(np.linalg.det(H[:2, :2]))))
    return {"inliers": inliers, "ink_agreement": round(float(agree), 3), "overlap": round(float(valid.mean()), 3),
            "scale": round(scale, 3)}
