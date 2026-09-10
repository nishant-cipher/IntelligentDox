"""Numeric and currency normalization utilities.

These functions are the single source of truth for turning messy
OCR/PDF text like "(1,250.50)", "Rs. 25,000", "USD 13,125.00" or
"₹ in crore" into clean Python numbers. Every rule here is driven by
what is explicitly present in the source text - nothing is invented.
"""
import re
from typing import List, Optional

CURRENCY_SYMBOLS = {
    "₹": "INR",
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "RM": "MYR",
}

CURRENCY_CODES = ["INR", "USD", "EUR", "GBP", "MYR", "RS", "RS.", "RM"]

_UNIT_MULTIPLIERS = {
    "thousand": 1_000,
    "thousands": 1_000,
    "lakh": 100_000,
    "lakhs": 100_000,
    "lac": 100_000,
    "lacs": 100_000,
    "million": 1_000_000,
    "millions": 1_000_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
    "billion": 1_000_000_000,
    "billions": 1_000_000_000,
}

_NUMBER_RE = re.compile(
    r"[-+]?\(?\s*[₹$€£]?\s*(?:RM|Rs\.?|INR|USD|EUR|GBP|MYR)?\s*"
    r"(\d{1,3}(?:[,\s]\d{2,3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*\)?",
    re.IGNORECASE,
)


def normalize_whitespace(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"[ \t]+", " ", text.replace("\xa0", " ")).strip()


def is_negative_representation(raw: str) -> bool:
    """True if parentheses/brackets or a leading minus mark a negative value."""
    raw = raw.strip()
    if not raw:
        return False
    if raw.startswith("(") and raw.endswith(")"):
        return True
    if raw.startswith("[") and raw.endswith("]"):
        return True
    if raw.startswith("-"):
        return True
    return False


def parse_number(raw: Optional[str], treat_lone_dash_as_zero: bool = False) -> Optional[float]:
    """Parse a numeric string into a float.

    Handles thousands separators, parentheses-as-negative, currency
    symbols/codes and stray whitespace inserted by OCR. Returns None
    when the text does not contain a usable number - callers must not
    substitute a guessed value.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None

    if text in {"-", "--", "—", "–", "NIL", "Nil", "nil"}:
        return 0.0 if treat_lone_dash_as_zero else None

    negative = is_negative_representation(text)

    cleaned = text.strip("()[]")
    cleaned = re.sub(r"(?i)\b(RS\.?|INR|USD|EUR|GBP|RM)\b", "", cleaned)
    cleaned = cleaned.replace("₹", "").replace("$", "").replace("€", "").replace("£", "")
    cleaned = cleaned.replace(",", "").replace(" ", "")
    cleaned = cleaned.strip()
    cleaned = cleaned.rstrip("-")  # trailing minus e.g. "500-"
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    if cleaned.startswith("-"):
        cleaned = cleaned[1:]

    if not cleaned or not re.fullmatch(r"\d+(\.\d+)?", cleaned):
        return None

    try:
        value = float(cleaned)
    except ValueError:
        return None

    return -value if negative and value != 0 else value


def detect_currency(text: Optional[str]) -> Optional[str]:
    """Return an ISO-like currency code found in text, else None."""
    if not text:
        return None
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in text:
            return code
    for code in ["INR", "USD", "EUR", "GBP", "MYR"]:
        if re.search(rf"\b{code}\b", text, re.IGNORECASE):
            return code
    if re.search(r"\bRs\.?\b", text, re.IGNORECASE):
        return "INR"
    return None


def detect_unit_multiplier(text: Optional[str]) -> tuple[Optional[float], Optional[str]]:
    """Detect an explicitly stated scale like '(₹ in crore)' or 'amounts in millions'.

    Only triggers on an explicit unit word in the text - never guessed.
    Returns (multiplier, unit_label) or (None, None).
    """
    if not text:
        return None, None
    lowered = text.lower()
    for unit, multiplier in sorted(_UNIT_MULTIPLIERS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{unit}\b", lowered):
            return float(multiplier), unit
    return None, None


def extract_first_number(text: Optional[str]) -> Optional[float]:
    """Find and parse the first numeric token embedded within free text."""
    if not text:
        return None
    match = _NUMBER_RE.search(text)
    if not match:
        return None
    start = match.start()
    negative = is_negative_representation(text[start:match.end()])
    value = parse_number(match.group(0))
    if value is None:
        return None
    if negative and value > 0:
        value = -value
    return value


# A genuine monetary figure is either thousands-grouped (>=1 comma, Western
# 3-digit or Indian 2-digit groupings both supported) or carries a decimal
# point; schedule/reference numbers like "18 (4)" or "2A" are always short,
# ungrouped, undecimaled integers, so they never match either alternative.
# Whitespace is tolerated around each comma because OCR on large numbers
# occasionally inserts a stray space (e.g. "8,923,441  ,607").
FINANCIAL_AMOUNT_RE = re.compile(
    r"\(?\s*-?\d{1,3}(?:\s*,\s*\d{2,3})+(?:\.\d{1,2})?\s*\)?"
    r"|\(?\s*-?\d+\.\d{1,2}\s*\)?"
    r"|(?<![A-Za-z0-9])-(?![A-Za-z0-9.])"
)

# Broader token used for invoice line items, where quantities are often
# plain integers with no decimal point.
GENERIC_NUMBER_RE = re.compile(r"\(?\s*-?\d{1,3}(?:,\d{2,3})*(?:\.\d{1,2})?\s*\)?")


def find_financial_amounts(line: str) -> List[float]:
    """Extract all decimal monetary tokens from a reconstructed table row,
    left-to-right, honoring parentheses-as-negative and a lone '-' as nil."""
    out: List[float] = []
    for raw in FINANCIAL_AMOUNT_RE.findall(line):
        value = parse_number(raw, treat_lone_dash_as_zero=True)
        if value is not None:
            out.append(value)
    return out


def find_generic_numbers(line: str) -> List[float]:
    out: List[float] = []
    for raw in GENERIC_NUMBER_RE.findall(line):
        value = parse_number(raw)
        if value is not None:
            out.append(value)
    return out


def numbers_match(calculated: Optional[float], reported: Optional[float], abs_tol: float, rel_tol: float) -> bool:
    """Sensible float comparison combining an absolute and a relative tolerance."""
    if calculated is None or reported is None:
        return False
    diff = abs(calculated - reported)
    if diff <= abs_tol:
        return True
    denom = max(abs(calculated), abs(reported), 1e-9)
    return (diff / denom) <= rel_tol
