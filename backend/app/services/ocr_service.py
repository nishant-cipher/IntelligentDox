"""Layered text extraction: native PDF text first, OCR fallback second.

Pipeline:
    PDF  -> PyMuPDF native text extraction -> quality check -> OCR fallback
             if the native layer is empty/poor (i.e. a scanned PDF)
    JPG/PNG -> OCR directly

Page numbers are preserved throughout so downstream extraction can cite
`page_number` evidence.
"""
import os
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import fitz  # PyMuPDF
import pytesseract
from PIL import Image, ImageOps
from pytesseract import Output

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


def _configure_tesseract() -> None:
    if settings.TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD
        return
    if platform.system() == "Windows":
        default_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        if os.path.exists(default_path):
            pytesseract.pytesseract.tesseract_cmd = default_path


_configure_tesseract()


@dataclass
class PageText:
    page_number: int
    text: str
    ocr_used: bool = False
    ocr_confidence: Optional[float] = None
    row_text: str = ""  # bounding-box reconstructed rows; best source for table parsing

    @property
    def table_lines(self) -> List[str]:
        return [l for l in self.row_text.splitlines() if l.strip()]


@dataclass
class DocumentText:
    pages: List[PageText] = field(default_factory=list)
    ocr_used: bool = False

    @property
    def full_text(self) -> str:
        return "\n".join(p.text for p in self.pages)

    def page_text(self, page_number: int) -> str:
        for p in self.pages:
            if p.page_number == page_number:
                return p.text
        return ""

    @property
    def full_row_text(self) -> str:
        return "\n".join(p.row_text for p in self.pages)


def get_pdf_page_count(pdf_path: str) -> int:
    with fitz.open(pdf_path) as doc:
        return doc.page_count


def is_text_quality_sufficient(text: str) -> bool:
    """Heuristic: enough alphanumeric content to trust native PDF text."""
    if not text:
        return False
    stripped = text.strip()
    if len(stripped) < settings.OCR_MIN_TEXT_CHARS:
        return False
    alnum_count = sum(1 for c in stripped if c.isalnum())
    return alnum_count >= settings.OCR_MIN_TEXT_CHARS * 0.5


def render_pdf_page(pdf_path: str, page_index: int, dpi: Optional[int] = None) -> Image.Image:
    """Render a single PDF page (0-indexed) to a PIL image at the given DPI."""
    dpi = dpi or settings.PDF_RENDER_DPI
    zoom = dpi / 72.0
    with fitz.open(pdf_path) as doc:
        page = doc[page_index]
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        mode = "RGB" if pix.alpha == 0 else "RGBA"
        img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
        if img.mode != "RGB":
            img = img.convert("RGB")
        return img


def _reconstruct_rows(data: dict, y_tolerance_ratio: float = 0.6) -> str:
    """Reconstruct visual table rows from Tesseract word boxes.

    Tesseract's own line/block/par grouping does layout analysis that can
    split a single visual table row (label | schedule | value | value)
    across different blocks when columns are separated by wide whitespace
    gutters. Clustering words purely by vertical pixel position and then
    sorting each cluster left-to-right reconstructs the row as a human
    would read it, which is essential for financial statement tables.
    """
    words = []
    n = len(data.get("text", []))
    for i in range(n):
        txt = (data["text"][i] or "").strip()
        conf_raw = data["conf"][i]
        try:
            conf = float(conf_raw)
        except (TypeError, ValueError):
            conf = -1
        if txt and conf >= 0:
            words.append(
                {
                    "text": txt,
                    "left": data["left"][i],
                    "top": data["top"][i],
                    "height": max(data["height"][i], 1),
                }
            )

    if not words:
        return ""

    heights = sorted(w["height"] for w in words)
    median_h = heights[len(heights) // 2] or 1
    tolerance = median_h * y_tolerance_ratio

    words_sorted = sorted(words, key=lambda w: w["top"])
    rows: List[List[dict]] = []
    current: List[dict] = []
    running_center: Optional[float] = None

    for w in words_sorted:
        center = w["top"] + w["height"] / 2
        if running_center is None or abs(center - running_center) <= tolerance:
            current.append(w)
            running_center = sum(x["top"] + x["height"] / 2 for x in current) / len(current)
        else:
            rows.append(current)
            current = [w]
            running_center = center
    if current:
        rows.append(current)

    lines = []
    for row in rows:
        row_sorted = sorted(row, key=lambda w: w["left"])
        lines.append("  ".join(w["text"] for w in row_sorted))
    return "\n".join(lines)


def run_ocr(image: Image.Image) -> tuple[str, Optional[float], str]:
    """Run Tesseract OCR on a PIL image.

    Returns (reading_order_text, avg_word_confidence, row_reconstructed_text).
    """
    try:
        text = pytesseract.image_to_string(image, lang=settings.OCR_LANGUAGE)
        data = pytesseract.image_to_data(image, lang=settings.OCR_LANGUAGE, output_type=Output.DICT)
        confidences = [float(c) for c in data.get("conf", []) if c not in ("-1", -1)]
        confidences = [c for c in confidences if c >= 0]
        avg_conf = sum(confidences) / len(confidences) if confidences else None
        row_text = _reconstruct_rows(data)
        return text, avg_conf, row_text
    except pytesseract.TesseractError as exc:
        logger.error("Tesseract OCR failed: %s", exc)
        return "", None, ""
    except Exception as exc:  # OCR must never crash the pipeline
        logger.error("Unexpected OCR failure: %s", exc)
        return "", None, ""


def extract_text_from_pdf(pdf_path: str) -> DocumentText:
    """Extract text per page: native text first, OCR fallback for scanned pages."""
    pages: List[PageText] = []
    any_ocr = False

    with fitz.open(pdf_path) as doc:
        page_count = doc.page_count
        native_texts = [doc[i].get_text() for i in range(page_count)]

    for idx, native_text in enumerate(native_texts):
        page_number = idx + 1
        if is_text_quality_sufficient(native_text):
            pages.append(PageText(page_number=page_number, text=native_text, ocr_used=False))
            continue

        logger.info("Page %d has insufficient native text; falling back to OCR", page_number)
        try:
            image = render_pdf_page(pdf_path, idx)
            ocr_text, ocr_conf, row_text = run_ocr(image)
        except Exception as exc:
            logger.error("Failed to render/OCR page %d: %s", page_number, exc)
            ocr_text, ocr_conf, row_text = native_text, None, ""
        any_ocr = True
        pages.append(
            PageText(
                page_number=page_number,
                text=ocr_text or native_text,
                ocr_used=True,
                ocr_confidence=ocr_conf,
                row_text=row_text,
            )
        )

    return DocumentText(pages=pages, ocr_used=any_ocr)


def extract_text_from_image(image_path: str) -> DocumentText:
    image = Image.open(image_path)
    # Phone cameras commonly store rotation as EXIF metadata rather than
    # rotating the pixels themselves; without correcting for it, OCR reads
    # sideways/upside-down photos as unreadable noise.
    image = ImageOps.exif_transpose(image)
    if image.mode != "RGB":
        image = image.convert("RGB")
    text, conf, row_text = run_ocr(image)
    page = PageText(page_number=1, text=text, ocr_used=True, ocr_confidence=conf, row_text=row_text)
    return DocumentText(pages=[page], ocr_used=True)


def extract_document_text(file_path: str, mime_type: str) -> DocumentText:
    if mime_type == "application/pdf":
        return extract_text_from_pdf(file_path)
    if mime_type in {"image/jpeg", "image/png"}:
        return extract_text_from_image(file_path)
    raise ValueError(f"Unsupported mime type for OCR: {mime_type}")
