"""Text helpers shared across extraction services: line search, label
matching and date normalization."""
import re
from typing import List, Optional

from dateutil import parser as dateutil_parser


def normalize_whitespace(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"[ \t]+", " ", text.replace("\xa0", " ")).strip()


def split_lines(text: str) -> List[str]:
    return [normalize_whitespace(l) for l in text.splitlines() if normalize_whitespace(l)]


def find_line(lines: List[str], *keywords: str, case_sensitive: bool = False) -> Optional[str]:
    """Return the first line containing all given keywords."""
    for line in lines:
        haystack = line if case_sensitive else line.lower()
        needles = keywords if case_sensitive else [k.lower() for k in keywords]
        if all(n in haystack for n in needles):
            return line
    return None


def find_line_index(lines: List[str], *keywords: str, case_sensitive: bool = False) -> Optional[int]:
    for idx, line in enumerate(lines):
        haystack = line if case_sensitive else line.lower()
        needles = keywords if case_sensitive else [k.lower() for k in keywords]
        if all(n in haystack for n in needles):
            return idx
    return None


def search_pattern(text: str, pattern: str, group: int = 1, flags=re.IGNORECASE) -> Optional[str]:
    match = re.search(pattern, text, flags)
    if not match:
        return None
    try:
        value = match.group(group)
    except IndexError:
        return None
    return normalize_whitespace(value) if value else None


_DATE_PATTERNS = [
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
    r"\b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b",
    r"\b\d{1,2}[-\s](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*[-\s]\d{2,4}\b",
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b",
    r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{4}\b",
]


def find_date_in_text(text: str) -> Optional[str]:
    """Find the first date-looking substring in text (original, un-normalized)."""
    for pattern in _DATE_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def normalize_date(raw: Optional[str]) -> Optional[str]:
    """Normalize a date string to YYYY-MM-DD when it can be confidently parsed.

    If parsing is not confident, returns the original (whitespace-cleaned)
    text rather than inventing a date.
    """
    if not raw:
        return None
    cleaned = normalize_whitespace(raw)
    if not cleaned:
        return None
    # Only trust a full parse when the text matches a recognizable date shape
    # (day + month + year all present) - otherwise dateutil would silently
    # fill in missing components (e.g. today's year), which counts as inventing data.
    if not find_date_in_text(cleaned):
        return cleaned
    try:
        dt = dateutil_parser.parse(cleaned, dayfirst=False, fuzzy=True)
    except (ValueError, OverflowError, TypeError):
        return cleaned
    try:
        return dt.date().isoformat()
    except Exception:
        return cleaned


def clean_ocr_artifacts(text: str) -> str:
    """Collapse repeated whitespace left behind by OCR spacing artifacts."""
    return re.sub(r"[ \t]{2,}", " ", text)
