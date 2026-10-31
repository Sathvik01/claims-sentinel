"""Error-level / JPEG-ghost analysis.

A JPEG page re-compressed at the quality it was last saved with barely changes ("ghost"). If a region was
pasted or retyped and the page re-exported (typically at a different quality), that region lacks the ghost
the rest of the page shows. Each block is compared only with itself across a quality sweep, so colour,
stamps and dense text don't masquerade as edits."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

QUALITIES = list(range(60, 99))
DIP_SPAN = 2
MIN_GHOST_DEPTH = 0.3
MIN_RESIDUAL = 0.45
BLOCK = 16
CONTENT_GRAD = 6.0  # mean edge energy for a block to count as content
Z_ANOMALY = 5.0
MIN_HOTSPOT_BLOCKS = 2
MIN_HOTSPOT_Z = 6.0  # colour-edge artefacts (dark table headers) sit around 5


@dataclass
class ElaResult:
    applicable: bool
    note: str
    ghost_quality: int | None = None
    content_blocks: int = 0
    anomalous_blocks: int = 0
    anomalous_fraction: float = 0.0
    peak_z: float = 0.0
    hotspots: list[dict] = field(default_factory=list)  # {x, y, w, h, blocks, z}
    heatmap_path: str | None = None


def _blocks(a: np.ndarray) -> np.ndarray:
    h, w = (a.shape[0] // BLOCK) * BLOCK, (a.shape[1] // BLOCK) * BLOCK
    return a[:h, :w].reshape(h // BLOCK, BLOCK, w // BLOCK, BLOCK).mean(axis=(1, 3))


def _resave(img: Image.Image, q: int) -> np.ndarray:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=q)
    return np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGB"), dtype=np.float32)


def error_level_analysis(image: Image.Image, original_bytes: bytes | None, raster_source: bool,
                         heatmap_path: Path) -> ElaResult:
    if not raster_source:
        return ElaResult(False, "Page is rendered from a digital PDF; compression traces are uniform by "
                                "construction, so error-level analysis is not meaningful.")
    if original_bytes and not original_bytes[:3] == b"\xff\xd8\xff":
        return ElaResult(False, "Image is not a JPEG (lossless format); error-level analysis needs JPEG traces.")
    src = Image.open(io.BytesIO(original_bytes)).convert("RGB") if original_bytes else image
    a = np.asarray(src, dtype=np.float32)
    gray = np.asarray(src.convert("L"), dtype=np.float32)
    grad = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)) + np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
    content = _blocks(grad) > CONTENT_GRAD
    if content.sum() < 40:
        return ElaResult(False, "Too little content on the page for compression statistics.", None, int(content.sum()))

    # The last save's quality is readable from the file's quantization tables.
    last_q = None
    if original_bytes:
        target = Image.open(io.BytesIO(original_bytes)).quantization
        for q in range(100, 9, -1):
            buf = io.BytesIO()
            src.save(buf, "JPEG", quality=q)
            if Image.open(io.BytesIO(buf.getvalue())).quantization == target:
                last_q = q
                break

    # Block error at each quality. A page compressed earlier at quality q0 and re-saved later shows a sharp
    # local dip ("ghost") at q0 in every untouched block; edited blocks were only saved at the later quality.
    qualities = [q for q in QUALITIES if last_q is None or q <= last_q - 4]
    errs = np.stack([_blocks(((a - _resave(src, q)) ** 2).mean(axis=2)) for q in qualities])
    depth = np.zeros_like(errs)
    for i in range(DIP_SPAN, len(qualities) - DIP_SPAN):
        around = (errs[i - DIP_SPAN] + errs[i + DIP_SPAN]) / 2
        depth[i] = 1 - errs[i] / (around + 1e-3)
    gi = int(np.argmax(np.median(depth[:, content], axis=1)))
    if float(np.median(depth[gi, content])) < MIN_GHOST_DEPTH:
        return ElaResult(True, f"Single compression (last saved at quality {last_q}); no earlier save to compare "
                               "against, so no edit traces.", last_q, int(content.sum()))
    # Per block: error at the ghost quality relative to the block's own mean error across the sweep.
    # Untouched blocks sit near 0 (deep ghost); edited blocks have no ghost and sit near 1.
    residual = errs[gi] / (errs.mean(axis=0) + 0.5)
    vals = residual[content]
    med = float(np.median(vals))
    mad = float(np.median(np.abs(vals - med))) or 1e-3
    z = np.where(content, (residual - med) / (1.4826 * mad), 0.0)
    anomalous = (z > Z_ANOMALY) & (residual > MIN_RESIDUAL) & content

    n, _, stats, _ = cv2.connectedComponentsWithStats(anomalous.astype(np.uint8), connectivity=8)
    hotspots = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < MIN_HOTSPOT_BLOCKS:
            continue
        zz = float(z[y:y + h, x:x + w][anomalous[y:y + h, x:x + w]].mean())
        if zz < MIN_HOTSPOT_Z:
            continue
        hotspots.append({"x": x * BLOCK, "y": y * BLOCK, "w": w * BLOCK, "h": h * BLOCK, "blocks": area,
                         "z": round(zz, 1)})
    hotspots.sort(key=lambda h: -h["blocks"] * h["z"])
    hotspots = hotspots[:5]
    flagged = sum(h["blocks"] for h in hotspots)

    # Heatmap: ghost residual upscaled over a faded page; hotspots outlined.
    zmap = np.clip(z / 10.0, 0, 1)
    heat_small = (zmap * 255).astype(np.uint8)
    heat = cv2.resize(heat_small, (heat_small.shape[1] * BLOCK, heat_small.shape[0] * BLOCK),
                      interpolation=cv2.INTER_NEAREST)
    heat = cv2.applyColorMap(heat, cv2.COLORMAP_INFERNO)
    base = cv2.cvtColor(np.asarray(src.convert("L"))[:heat.shape[0], :heat.shape[1]], cv2.COLOR_GRAY2BGR)
    out = cv2.addWeighted(base, 0.45, heat, 0.55, 0)
    for h in hotspots:
        cv2.rectangle(out, (h["x"], h["y"]), (h["x"] + h["w"], h["y"] + h["h"]), (80, 220, 255), 3)
    heatmap_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(heatmap_path), out)

    return ElaResult(True, f"Re-saved at quality {last_q} after an earlier save at {qualities[gi]}", qualities[gi], int(content.sum()), flagged,
                     round(flagged / int(content.sum()), 4), round(float(z.max()), 1), hotspots, str(heatmap_path))
