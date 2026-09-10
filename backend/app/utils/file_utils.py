"""Safe file handling: extension checks, magic-byte sniffing, safe temp paths.

Never trusts the client-supplied filename or Content-Type header alone -
file identity is confirmed by inspecting the actual byte signature.
"""
import re
import uuid
from pathlib import Path
from typing import Optional

_MAGIC_SIGNATURES = {
    b"%PDF": "application/pdf",
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
}

EXTENSION_TO_MIME = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


def sniff_mime_type(content: bytes) -> Optional[str]:
    """Identify a file's real type from its magic bytes, ignoring the extension."""
    for signature, mime in _MAGIC_SIGNATURES.items():
        if content.startswith(signature):
            return mime
    return None


def get_extension(filename: str) -> str:
    return Path(filename).suffix.lower()


def safe_filename(original_name: str) -> str:
    """Build a filesystem-safe, collision-resistant name for temporary storage.

    Strips directory components (no path traversal) and disallowed characters,
    prefixes a UUID so concurrent uploads with the same name never collide.
    """
    base = Path(original_name).name  # strips any directory / traversal component
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    if not base:
        base = "upload"
    return f"{uuid.uuid4().hex}_{base}"


def human_readable_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"
