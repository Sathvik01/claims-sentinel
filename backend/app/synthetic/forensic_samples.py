"""Image samples for the document-integrity skills.

documents/images/  bill_genuine.jpg            untouched scan-like JPEG of a new bill
                   bill_tampered_amount.jpg    same bill, total + one line amount edited, re-saved (ELA target)
                   bill_cloned_stamp.jpg       approval stamp copied to a second place (copy-move target)
                   bill_reused_resized.jpg     a bill already paid in history, resized and re-saved
                   bill_reused_cropped.jpg     the same paid bill, cropped
documents/seed/    {claim_id}.jpg              bills of paid claims, fingerprinted by "Load demo data"
"""

from __future__ import annotations

import io
import random
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont

SCAN_QUALITY = 78


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("arialbd.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def render_pdf(pdf_path: Path, dpi: int = 150) -> Image.Image:
    pdf = pdfium.PdfDocument(str(pdf_path))
    img = pdf[0].render(scale=dpi / 72).to_pil().convert("RGB")
    pdf.close()
    return img


def scanify(img: Image.Image, seed: int) -> Image.Image:
    """Light paper tint + sensor noise so the page behaves like a phone scan, not a vector render."""
    rnd = random.Random(seed)
    img = img.copy()
    px = img.load()
    w, h = img.size
    for _ in range(w * h // 40):
        x, y = rnd.randrange(w), rnd.randrange(h)
        r, g, b = px[x, y]
        d = rnd.randint(-14, 14)
        px[x, y] = (max(0, min(255, r + d - 3)), max(0, min(255, g + d - 2)), max(0, min(255, b + d - 6)))
    return img


def jpeg(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def draw_stamp(img: Image.Image, center: tuple[int, int], seed: int) -> tuple[int, int, int, int]:
    """Round approval seal with text and a textured ring (gives the clone detector keypoints)."""
    rnd = random.Random(seed)
    d = ImageDraw.Draw(img)
    cx, cy = center
    r = 70
    col = (32, 74, 168)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=5)
    d.ellipse([cx - r + 12, cy - r + 12, cx + r - 12, cy + r - 12], outline=col, width=2)
    for _ in range(120):  # ink texture inside the ring
        a, b = rnd.randint(-r + 14, r - 14), rnd.randint(-r + 14, r - 14)
        if a * a + b * b < (r - 16) ** 2:
            d.point((cx + a, cy + b), fill=col)
    f = _font(15)
    d.text((cx, cy - 16), "CASHLESS", fill=col, font=f, anchor="mm")
    d.text((cx, cy + 4), "APPROVED", fill=col, font=f, anchor="mm")
    d.text((cx, cy + 26), "TPA DESK 07", fill=col, font=_font(11), anchor="mm")
    return (cx - r, cy - r, cx + r, cy + r)


def edit_amount(img: Image.Image, box: tuple[int, int, int, int], text: str) -> None:
    d = ImageDraw.Draw(img)
    d.rectangle(box, fill=(255, 255, 255))
    d.text((box[2] - 4, (box[1] + box[3]) // 2), text, fill=(10, 10, 10), font=_font(18), anchor="rm")


def write_forensic_samples(out: Path, genuine_pdf: Path, reused_pdf: Path, seed_pdfs: dict[str, Path]) -> dict:
    img_dir, seed_dir = out / "images", out / "seed"
    img_dir.mkdir(parents=True, exist_ok=True)
    seed_dir.mkdir(parents=True, exist_ok=True)

    # Genuine new bill, stamped, saved as a scan.
    base = scanify(render_pdf(genuine_pdf), seed=1)
    w, h = base.size
    draw_stamp(base, (int(w * 0.78), int(h * 0.80)), seed=7)
    genuine = jpeg(base, SCAN_QUALITY)
    (img_dir / "bill_genuine.jpg").write_bytes(genuine)

    # Tampered: open the scan, white-out two amounts, type new ones, save again (single vs double compression).
    tam = Image.open(io.BytesIO(genuine)).convert("RGB")
    boxes = [(int(w * 0.80), int(h * 0.555), int(w * 0.95), int(h * 0.575)),
             (int(w * 0.805), int(h * 0.4255), int(w * 0.925), int(h * 0.4405))]
    edit_amount(tam, boxes[0], "Rs. 1,98,880.00")
    edit_amount(tam, boxes[1], "Rs. 94,000.00")
    (img_dir / "bill_tampered_amount.jpg").write_bytes(jpeg(tam, 95))  # editor export quality

    # Copy-move: copy the stamp region to a second, diagonal position.
    cl = Image.open(io.BytesIO(genuine)).convert("RGB")
    sx, sy = int(w * 0.78), int(h * 0.80)
    patch = cl.crop((sx - 75, sy - 75, sx + 75, sy + 75))
    cl.paste(patch, (int(w * 0.18) - 75, int(h * 0.88) - 75))
    (img_dir / "bill_cloned_stamp.jpg").write_bytes(jpeg(cl, SCAN_QUALITY))

    # Seeds: bills of paid claims, as the insurer's document store would hold them.
    for claim_id, pdf in seed_pdfs.items():
        (seed_dir / f"{claim_id}.jpg").write_bytes(jpeg(scanify(render_pdf(pdf), seed=hash(claim_id) % 1000), SCAN_QUALITY))

    # Reused: an already-paid bill resubmitted after resizing / cropping.
    paid = Image.open(seed_dir / f"{reused_pdf.stem.split('_')[-1]}.jpg").convert("RGB")
    pw, ph = paid.size
    (img_dir / "bill_reused_resized.jpg").write_bytes(jpeg(paid.resize((int(pw * 0.85), int(ph * 0.85))), 70))
    (img_dir / "bill_reused_cropped.jpg").write_bytes(
        jpeg(paid.crop((int(pw * 0.06), int(ph * 0.05), int(pw * 0.94), int(ph * 0.90))), 82))
    return {"edited_boxes": boxes, "stamp_center": (sx, sy)}
