"""Explainable confidence scoring and field construction helpers.

Confidence is never a random number. It is derived from concrete signals:
  - how specific the pattern/label match was (exact labeled match vs. a
    loose fallback heuristic)
  - OCR word-level confidence reported by Tesseract for the source region
  - whether a numeric value actually parsed successfully
  - whether grounding evidence text is available at all

Each of these is a real, inspectable number; `combine_confidence` just
averages the ones that apply and clamps to [0, 1].
"""
from typing import Any, Dict, List, Optional

STRICT_LABEL_MATCH = 0.95
LOOSE_LABEL_MATCH = 0.7
FALLBACK_MATCH = 0.5


def combine_confidence(*components: Optional[float]) -> Optional[float]:
    values = [c for c in components if c is not None]
    if not values:
        return None
    avg = sum(values) / len(values)
    return round(max(0.0, min(1.0, avg)), 4)


def ocr_quality_score(avg_word_confidence_0_100: Optional[float]) -> Optional[float]:
    """Convert Tesseract's 0-100 average word confidence into a 0-1 score."""
    if avg_word_confidence_0_100 is None:
        return None
    return round(max(0.0, min(1.0, avg_word_confidence_0_100 / 100.0)), 4)


def make_field(
    value: Any,
    confidence: Optional[float] = None,
    page_number: Optional[int] = None,
    evidence: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the standard {value, confidence, page_number, evidence} shape.

    `value` is None when the field genuinely could not be read - callers
    must never substitute a guessed value here.
    """
    field: Dict[str, Any] = {"value": value}
    if confidence is not None:
        field["confidence"] = confidence
    if page_number is not None:
        field["page_number"] = page_number
    if evidence is not None:
        field["evidence"] = evidence
    return field


def overall_confidence(field_confidences: List[Optional[float]]) -> Optional[float]:
    values = [c for c in field_confidences if c is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 4)
