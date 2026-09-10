"""File validation: extension, magic-byte MIME sniffing, integrity, page count.

Kept deliberately separate from OCR/extraction - this service only answers
"is this a file we can safely attempt to process?".
"""
from dataclasses import dataclass
from typing import Optional

import fitz  # PyMuPDF
from PIL import Image, UnidentifiedImageError

from app.core.config import get_settings
from app.core.exceptions import (
    CorruptedFileError,
    EmptyFileError,
    FileTooLargeError,
    PageLimitExceededError,
    UnsupportedFileTypeError,
)
from app.core.logging import get_logger
from app.utils.file_utils import EXTENSION_TO_MIME, get_extension, sniff_mime_type

logger = get_logger(__name__)
settings = get_settings()


@dataclass
class FileValidationResult:
    file_type: str
    is_supported: bool
    is_readable: bool
    page_count: int
    status: str  # PASS | FAIL
    issues: list


def validate_upload(filename: str, content: bytes) -> FileValidationResult:
    """Validate an uploaded file's type, integrity and page count.

    Raises a controlled `AppError` subclass on any hard failure so the API
    layer can return `{"error": {...}}` with the right HTTP status.
    """
    if not content:
        raise EmptyFileError()

    size_mb = len(content) / (1024 * 1024)
    if size_mb > settings.MAX_FILE_SIZE_MB:
        raise FileTooLargeError(
            f"File size {size_mb:.1f}MB exceeds the maximum allowed {settings.MAX_FILE_SIZE_MB}MB."
        )

    extension = get_extension(filename)
    if extension not in settings.ALLOWED_EXTENSIONS:
        raise UnsupportedFileTypeError(
            f"Extension '{extension}' is not supported. Allowed types: PDF, JPG, JPEG, PNG."
        )

    sniffed_mime = sniff_mime_type(content)
    expected_mime = EXTENSION_TO_MIME.get(extension)
    if sniffed_mime is None or sniffed_mime != expected_mime:
        raise UnsupportedFileTypeError(
            "The file content does not match a supported PDF/JPG/PNG format "
            "(extension and actual file signature disagree)."
        )

    if sniffed_mime == "application/pdf":
        page_count = _validate_pdf(content)
    else:
        page_count = _validate_image(content)

    if page_count > settings.MAX_PAGES:
        raise PageLimitExceededError(
            f"Document has {page_count} pages; the maximum allowed is {settings.MAX_PAGES}."
        )

    return FileValidationResult(
        file_type=sniffed_mime,
        is_supported=True,
        is_readable=True,
        page_count=page_count,
        status="PASS",
        issues=[],
    )


def _validate_pdf(content: bytes) -> int:
    try:
        doc = fitz.open(stream=content, filetype="pdf")
        page_count = doc.page_count
        if page_count == 0:
            doc.close()
            raise CorruptedFileError("The PDF contains no pages.")
        # Touch the first page to confirm the file is actually decodable.
        _ = doc[0].get_text()
        doc.close()
        return page_count
    except CorruptedFileError:
        raise
    except Exception as exc:
        logger.error("PDF integrity check failed: %s", exc)
        raise CorruptedFileError("The PDF file appears to be corrupted or malformed.")


def _validate_image(content: bytes) -> int:
    import io

    try:
        image = Image.open(io.BytesIO(content))
        image.verify()
        # Re-open after verify(); verify() leaves the file unusable for further ops.
        image = Image.open(io.BytesIO(content))
        image.load()
        return 1
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        logger.error("Image integrity check failed: %s", exc)
        raise CorruptedFileError("The image file appears to be corrupted or unreadable.")
