"""Optional LLM-assisted extraction.

Disabled unless `LLM_PROVIDER=anthropic` and `LLM_API_KEY` are set in the
environment. The deterministic, rule-based pipeline in `extraction_service.py`
is the primary extraction path and is fully functional on its own - this
module only ever *supplements* it, filling in fields the deterministic pass
left null. It never overrides a value the deterministic pass already found,
and any failure here (timeout, malformed JSON, network error) is caught and
logged - it can never crash or degrade the main processing flow.

Note: this module was implemented against the documented Anthropic Messages
API but could not be exercised end-to-end in this environment (no API key
was configured), so treat it as implemented-but-unverified until run against
a real key.
"""
import json
import re
from typing import Any, Dict, Optional

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

_SCHEMA_HINTS = {
    "invoice": (
        "invoice_number, invoice_date, vendor_name, customer_name, currency, subtotal, "
        "tax_amount, discount, total_amount, due_date, purchase_order_number, payment_terms, "
        "tax_id, phone, email, line_items (array of {description, quantity, unit_price, amount})"
    ),
    "balance_sheet": (
        "statement_title, company_name, reporting_date, currency, units, periods (array of period "
        "labels), financial_line_items (array of {section, name, values: {period: number}}), "
        "totals: {total_assets, total_liabilities, total_equity} each keyed by period"
    ),
    "profit_and_loss": (
        "statement_title, company_name, reporting_period, currency, units, periods, "
        "financial_line_items (array of {section, name, values: {period: number}}), "
        "totals keyed by period for revenue/interest_earned/other_income/total_income/"
        "interest_expended/operating_expenses/provisions_and_contingencies/total_expenditure/"
        "net_profit_before_minority_interest/minority_interest/net_profit_attributable_to_group/"
        "brought_forward_profit/total_available_for_appropriation/cost_of_sales/gross_profit/"
        "operating_profit/tax/net_profit"
    ),
    "cash_flow_statement": (
        "statement_title, company_name, reporting_period, currency, units, periods, "
        "financial_line_items (array of {section, name, values: {period: number}}), "
        "totals keyed by period for operating_cash_flow/investing_cash_flow/financing_cash_flow/"
        "fx_translation_adjustment/net_change_in_cash/opening_cash/closing_cash"
    ),
}

_SYSTEM_PROMPT = """You are a precise document data extraction engine. You will be given the raw \
OCR/text of a financial document. Extract ONLY values that are explicitly present in the text.

Rules (follow exactly):
1. Never infer, guess, or hallucinate a value. If a field is not clearly present, set it to null.
2. Parentheses or brackets around a number mean it is NEGATIVE, e.g. "(1,250.50)" -> -1250.50.
3. Preserve every comparative period/column found (e.g. multiple years side by side) - do not
   collapse them into one value.
4. Preserve table rows and columns; do not discard line items.
5. For each important field, include an `evidence` string with the literal source text you read
   it from, and a `page_number` if determinable.
6. Return ONLY valid JSON matching the field list given - no markdown, no commentary, no code fences.
"""


def is_enabled() -> bool:
    return settings.LLM_PROVIDER.lower() == "anthropic" and bool(settings.LLM_API_KEY)


def _build_user_prompt(document_type: str, text: str) -> str:
    schema_hint = _SCHEMA_HINTS.get(document_type, "")
    return (
        f"Document type: {document_type}\n"
        f"Expected fields: {schema_hint}\n\n"
        f"Document text (from OCR/native PDF extraction):\n---\n{text[:12000]}\n---\n\n"
        "Return a single JSON object with the expected fields."
    )


def _parse_json_response(raw: str) -> Optional[Dict[str, Any]]:
    raw = raw.strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    candidate = match.group(0) if match else raw
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        logger.error("LLM response was not valid JSON: %s", exc)
        return None


def extract_with_llm(document_type: str, text: str) -> Optional[Dict[str, Any]]:
    """Best-effort LLM extraction. Returns None on any failure - callers must
    treat None as "no supplementary data available", never as an error to
    propagate."""
    if not is_enabled():
        return None

    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package not installed; skipping LLM extraction")
        return None

    try:
        client = anthropic.Anthropic(api_key=settings.LLM_API_KEY, timeout=settings.LLM_TIMEOUT_SECONDS)
        response = client.messages.create(
            model=settings.LLM_MODEL,
            max_tokens=4096,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_prompt(document_type, text)}],
        )
        raw_text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        parsed = _parse_json_response(raw_text)
        if parsed is None:
            return None
        logger.info("LLM extraction succeeded | document_type=%s | fields=%d", document_type, len(parsed))
        return parsed
    except Exception as exc:  # network error, timeout, auth failure, etc. - never crash the pipeline
        logger.error("LLM extraction failed, continuing with deterministic result only: %s", exc)
        return None


def merge_gaps(deterministic: Dict[str, Any], llm_result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Fill only the null/missing fields of the deterministic result from the
    LLM result. Never overrides a value the deterministic pipeline already
    found - the rule-based pass is trusted first because it is grounded in
    exact regex/positional matches against the OCR text."""
    if not llm_result:
        return deterministic

    for key, llm_value in llm_result.items():
        if key not in deterministic:
            continue
        current = deterministic[key]
        if isinstance(current, dict) and "value" in current:
            if current.get("value") in (None, ""):
                new_value = llm_value.get("value") if isinstance(llm_value, dict) else llm_value
                if new_value not in (None, ""):
                    deterministic[key] = {
                        "value": new_value,
                        "confidence": 0.5,
                        "evidence": llm_value.get("evidence") if isinstance(llm_value, dict) else None,
                        "page_number": llm_value.get("page_number") if isinstance(llm_value, dict) else None,
                    }
    return deterministic
