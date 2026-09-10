"""Tests for document_validation_service: file type, integrity and page-count rules."""
import io

import fitz
import pytest
from PIL import Image

from app.core.exceptions import (
    CorruptedFileError,
    EmptyFileError,
    PageLimitExceededError,
    UnsupportedFileTypeError,
)
from app.services.document_validation_service import validate_upload

from .conftest import dataset_file, skip_if_no_dataset


def _make_png_bytes(width=200, height=100) -> bytes:
    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_pdf_bytes(num_pages: int) -> bytes:
    doc = fitz.open()
    for _ in range(num_pages):
        page = doc.new_page()
        page.insert_text((72, 72), "Sample text for validation testing.")
    data = doc.tobytes()
    doc.close()
    return data


class TestValidUploads:
    @skip_if_no_dataset
    def test_valid_pdf_from_dataset(self):
        path = dataset_file("Balance Sheet", "Consolidated Balance Sheet 2026.pdf")
        content = path.read_bytes()
        result = validate_upload(path.name, content)
        assert result.status == "PASS"
        assert result.file_type == "application/pdf"
        assert result.page_count == 1
        assert result.is_readable is True

    @skip_if_no_dataset
    def test_valid_jpg_from_dataset(self):
        path = dataset_file("Invoices", "X51005361895.jpg")
        content = path.read_bytes()
        result = validate_upload(path.name, content)
        assert result.status == "PASS"
        assert result.file_type == "image/jpeg"
        assert result.page_count == 1

    def test_valid_png_generated_for_testing(self):
        # No PNGs exist in the source dataset; a synthetic one is the only way
        # to exercise the PNG code path, so it is generated here in-memory.
        content = _make_png_bytes()
        result = validate_upload("sample.png", content)
        assert result.status == "PASS"
        assert result.file_type == "image/png"


class TestRejectedUploads:
    def test_unsupported_extension_rejected(self):
        with pytest.raises(UnsupportedFileTypeError):
            validate_upload("notes.txt", b"just some plain text content")

    def test_empty_file_rejected(self):
        with pytest.raises(EmptyFileError):
            validate_upload("empty.pdf", b"")

    def test_corrupted_pdf_rejected(self):
        with pytest.raises(CorruptedFileError):
            validate_upload("broken.pdf", b"%PDF-1.4\nthis is not a real pdf body")

    def test_extension_mime_mismatch_rejected(self):
        # Real PNG bytes but a .pdf extension - extension and content disagree.
        png_bytes = _make_png_bytes()
        with pytest.raises(UnsupportedFileTypeError):
            validate_upload("fake.pdf", png_bytes)

    def test_pdf_over_page_limit_rejected(self):
        # 4 pages > MAX_PAGES(3). Synthetic PDF built purely to exercise the
        # page-limit rule; no dataset file exceeds the 3-page limit.
        four_page_pdf = _make_pdf_bytes(4)
        with pytest.raises(PageLimitExceededError):
            validate_upload("too_long.pdf", four_page_pdf)

    def test_pdf_within_page_limit_accepted(self):
        two_page_pdf = _make_pdf_bytes(2)
        result = validate_upload("ok.pdf", two_page_pdf)
        assert result.page_count == 2
        assert result.status == "PASS"
