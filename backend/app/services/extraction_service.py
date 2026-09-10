"""Document-type-specific structured extraction.

Turns raw OCR/native text into the structured dicts that get stored as
`extracted_data`. Purely rule-based (regex + table-row heuristics) so it
works deterministically without any external LLM, and is fully generic -
nothing here is keyed off a specific filename or hardcoded dataset value.

Financial statements (balance sheet / P&L / cash flow) are parsed from the
OCR bounding-box row reconstruction (`ocr_service._reconstruct_rows`,
exposed as `PageText.row_text`) because Tesseract's own block/line grouping
frequently splits a single visual table row (label | schedule | value |
value) into separate blocks when columns are far apart. Reconstructing rows
by vertical pixel position and sorting left-to-right restores correct
reading order - see docs/dataset_analysis.md for the evidence.
"""
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.services.ocr_service import DocumentText
from app.utils.confidence_utils import (
    FALLBACK_MATCH,
    LOOSE_LABEL_MATCH,
    STRICT_LABEL_MATCH,
    combine_confidence,
    make_field,
    overall_confidence,
)
from app.utils.number_utils import (
    FINANCIAL_AMOUNT_RE,
    GENERIC_NUMBER_RE,
    detect_currency,
    detect_unit_multiplier,
    find_financial_amounts,
    find_generic_numbers,
    parse_number,
)
from app.utils.text_utils import (
    find_date_in_text,
    find_line,
    normalize_date,
    normalize_whitespace,
    search_pattern,
    split_lines,
)

INDIAN_SCALE_UNITS = {"crore", "crores", "lakh", "lakhs", "lac", "lacs"}


# ==========================================================================
# Shared helpers for financial statements
# ==========================================================================

def _header_zone_lines(doc_text: DocumentText, max_lines: int = 25) -> List[str]:
    first_page = doc_text.pages[0] if doc_text.pages else None
    if not first_page:
        return []
    lines = first_page.table_lines or split_lines(first_page.text)
    return lines[:max_lines]


def detect_periods(doc_text: DocumentText) -> List[str]:
    """Detect comparative period labels (years) from the statement header."""
    header_lines = _header_zone_lines(doc_text)
    years: List[str] = []
    for line in header_lines:
        for y in re.findall(r"\b(19\d{2}|20\d{2})\b", line):
            if y not in years:
                years.append(y)
    return years


def detect_currency_and_units(doc_text: DocumentText) -> Tuple[Optional[str], Optional[str], Optional[float], Optional[str]]:
    """Returns (currency_code, currency_evidence, unit_multiplier, unit_label)."""
    header_lines = _header_zone_lines(doc_text, max_lines=15)
    header_text = " ".join(header_lines)
    currency = detect_currency(header_text)
    multiplier, unit_label = detect_unit_multiplier(header_text)
    if multiplier is None:
        # Apostrophe-thousands notation ("in '000") - OCR frequently mangles
        # the apostrophe into a stray/unrecognized glyph, so match loosely.
        if re.search(r"in\s*.{0,2}000\b", header_text, re.IGNORECASE):
            multiplier, unit_label = 1000.0, "thousand"
    evidence = None
    for line in header_lines:
        if currency and detect_currency(line):
            evidence = line
            break
        if unit_label and unit_label.lower() in line.lower():
            evidence = line
        elif unit_label and re.search(r"in\s*.{0,2}000\b", line, re.IGNORECASE):
            evidence = line
    if currency is None and unit_label in INDIAN_SCALE_UNITS:
        currency = "INR"
    return currency, evidence, multiplier, unit_label


def detect_statement_title(doc_text: DocumentText, keywords: List[str]) -> Optional[Dict[str, Any]]:
    header_lines = _header_zone_lines(doc_text, max_lines=10)
    for idx, line in enumerate(header_lines):
        upper = line.upper()
        if any(k in upper for k in keywords):
            return make_field(normalize_whitespace(line), confidence=STRICT_LABEL_MATCH, page_number=1, evidence=line)
    return None


def detect_reporting_period(doc_text: DocumentText) -> Optional[Dict[str, Any]]:
    header_lines = _header_zone_lines(doc_text, max_lines=10)
    for line in header_lines:
        if re.search(r"\b(as at|as on|for the year ended|year ended)\b", line, re.IGNORECASE):
            date_str = find_date_in_text(line)
            value = normalize_date(date_str) if date_str else normalize_whitespace(line)
            return make_field(value, confidence=STRICT_LABEL_MATCH, page_number=1, evidence=line)
    return None


_COMPANY_NAME_RE = re.compile(r"^(?!For\b)([A-Z][A-Za-z&.,'\s]{2,60}(?:Limited|Ltd\.?|LLC|Inc\.?|Bank\b))$")


def detect_company_name(doc_text: DocumentText) -> Optional[Dict[str, Any]]:
    for page in doc_text.pages:
        lines = page.table_lines or split_lines(page.text)
        for line in lines:
            match = _COMPANY_NAME_RE.match(normalize_whitespace(line))
            if match:
                return make_field(
                    normalize_whitespace(match.group(1)),
                    confidence=STRICT_LABEL_MATCH,
                    page_number=page.page_number,
                    evidence=line,
                )
    return None


_TOTAL_LABEL_RE = re.compile(r"(?i)^\**\s*total\b")


def is_total_label(name: str) -> bool:
    return bool(_TOTAL_LABEL_RE.match(name.strip()))


def collect_table_rows(doc_text: DocumentText) -> List[Tuple[int, str]]:
    rows: List[Tuple[int, str]] = []
    for page in doc_text.pages:
        lines = page.table_lines or split_lines(page.text)
        for line in lines:
            rows.append((page.page_number, line))
    return rows


def parse_financial_line_items(
    doc_text: DocumentText,
    periods: List[str],
    classify_section: Callable[[str], Optional[str]],
) -> List[Dict[str, Any]]:
    """Walk reconstructed table rows, tracking the current section header and
    turning each row that carries `len(periods)` trailing monetary amounts
    into a structured line item."""
    line_items: List[Dict[str, Any]] = []
    current_section: Optional[str] = None
    num_periods = max(len(periods), 1)

    for page_number, raw_line in collect_table_rows(doc_text):
        # Row reconstruction joins words with double spaces; collapse to single
        # spaces so multi-word header substring checks (e.g. "OPERATING ACTIVIT")
        # match regardless of the source spacing.
        line = normalize_whitespace(raw_line)
        if not line:
            continue

        matches = list(FINANCIAL_AMOUNT_RE.finditer(line))

        if not matches:
            section = classify_section(line)
            if section:
                current_section = section
            continue

        amount_values = [parse_number(m.group(0), treat_lone_dash_as_zero=True) for m in matches]
        amount_values = [v for v in amount_values if v is not None]
        if not amount_values:
            continue

        take = min(num_periods, len(amount_values))
        period_values = amount_values[-take:]
        matched_periods = periods[-take:] if periods else [f"period_{i+1}" for i in range(take)]
        values = {p: v for p, v in zip(matched_periods, period_values)}

        label_end = matches[-take].start() if take <= len(matches) else matches[0].start()
        label = normalize_whitespace(line[:label_end])
        label = re.sub(r"\s+\d{1,3}[A-Za-z]?(?:\s*\(\d+\))?$", "", label).strip()

        if not label or not re.search(r"[A-Za-z]{2,}", label):
            continue

        is_total = is_total_label(label)
        line_items.append(
            {
                "section": current_section,
                "name": label,
                "values": values,
                "page_number": page_number,
                "evidence": line,
                "is_total": is_total,
            }
        )
        if is_total:
            # Rows after a section's grand total (e.g. memo items like
            # "Contingent liabilities") don't belong to that section anymore.
            current_section = None

    return line_items


def apply_totals_aliases(
    line_items: List[Dict[str, Any]], rules: List[Tuple[Callable[[Dict[str, Any]], bool], str]]
) -> Dict[str, Dict[str, float]]:
    totals: Dict[str, Dict[str, float]] = {}
    for item in line_items:
        for predicate, key in rules:
            if key in totals:
                continue
            try:
                if predicate(item):
                    totals[key] = item["values"]
                    break
            except Exception:
                continue
    return totals


# ==========================================================================
# Balance sheet
# ==========================================================================

def _classify_balance_sheet_section(label: str) -> Optional[str]:
    upper = label.upper()
    if "LIABILIT" in upper or ("EQUITY" in upper and "LIABILIT" in upper) or "CAPITAL AND" in upper:
        return "liabilities_and_equity"
    if "ASSET" in upper:
        return "assets"
    return None


def extract_balance_sheet(doc_text: DocumentText) -> Dict[str, Any]:
    periods = detect_periods(doc_text)
    currency, currency_evidence, unit_multiplier, unit_label = detect_currency_and_units(doc_text)
    line_items = parse_financial_line_items(doc_text, periods, _classify_balance_sheet_section)

    total_rules = [
        (lambda it: it["section"] == "assets" and it["is_total"], "total_assets"),
        (lambda it: it["section"] == "liabilities_and_equity" and it["is_total"], "total_liabilities"),
        (lambda it: re.search(r"total\s+equity|total\s+shareholders|shareholders'?\s+funds\s+total", it["name"].lower()), "total_equity"),
    ]
    totals = apply_totals_aliases(line_items, total_rules)

    field_confidences = []
    title_field = detect_statement_title(doc_text, ["BALANCE SHEET"])
    company_field = detect_company_name(doc_text)
    period_field = detect_reporting_period(doc_text)
    for f in (title_field, company_field, period_field):
        if f:
            field_confidences.append(f.get("confidence"))

    line_item_conf = STRICT_LABEL_MATCH if line_items else None
    if line_item_conf:
        field_confidences.append(line_item_conf)

    data: Dict[str, Any] = {
        "statement_title": title_field,
        "company_name": company_field,
        "reporting_date": period_field,
        "currency": make_field(currency, confidence=STRICT_LABEL_MATCH if currency else None, evidence=currency_evidence) if currency else make_field(None),
        "units": make_field(unit_label, confidence=STRICT_LABEL_MATCH if unit_label else None) if unit_label else make_field(None),
        "unit_multiplier": unit_multiplier,
        "periods": periods,
        "financial_line_items": line_items,
        "totals": {
            "total_assets": totals.get("total_assets", {}),
            "total_liabilities": totals.get("total_liabilities", {}),
            "total_equity": totals.get("total_equity", {}),
        },
    }
    data["_confidence_components"] = field_confidences
    return data


# ==========================================================================
# Profit & Loss
# ==========================================================================

def _classify_pl_section(label: str) -> Optional[str]:
    """Classify a header-candidate line (already confirmed to carry no
    monetary amounts) into a P&L section. Matches by keyword substring only -
    OCR is unreliable on the leading roman numeral (I/II/III/IV), so the
    numeral is deliberately not part of the match."""
    upper = label.upper()
    if "APPROPRIATION" in upper:
        return "appropriations"
    if "EXPENDITURE" in upper:
        return "expenditure"
    if "EARNING" in upper and "SHARE" in upper:
        return "eps"
    if "INCOME" in upper:
        return "income"
    if "PROFIT" in upper:
        return "profit"
    return None


def extract_profit_and_loss(doc_text: DocumentText) -> Dict[str, Any]:
    periods = detect_periods(doc_text)
    currency, currency_evidence, unit_multiplier, unit_label = detect_currency_and_units(doc_text)
    line_items = parse_financial_line_items(doc_text, periods, _classify_pl_section)

    def name_of(it):
        return it["name"].lower()

    total_rules: List[Tuple[Callable[[Dict[str, Any]], bool], str]] = [
        (lambda it: it["section"] == "income" and it["is_total"], "total_income"),
        (lambda it: "interest earned" in name_of(it), "interest_earned"),
        (lambda it: "other income" in name_of(it), "other_income"),
        (lambda it: it["section"] == "expenditure" and it["is_total"], "total_expenditure"),
        (lambda it: "interest expended" in name_of(it), "interest_expended"),
        (lambda it: "operating expenses" in name_of(it), "operating_expenses"),
        (lambda it: "provision" in name_of(it) and "conting" in name_of(it), "provisions_and_contingencies"),
        (lambda it: it["section"] == "profit" and "before minority interest" in name_of(it), "net_profit_before_minority_interest"),
        (lambda it: it["section"] == "profit" and "minority interest" in name_of(it) and name_of(it).startswith("less"), "minority_interest"),
        (lambda it: "attributable to the group" in name_of(it) or "attributable to group" in name_of(it), "net_profit_attributable_to_group"),
        (lambda it: "brought forward" in name_of(it), "brought_forward_profit"),
        (lambda it: it["section"] == "profit" and it["is_total"], "total_available_for_appropriation"),
        (lambda it: it["section"] == "appropriations" and it["is_total"], "total_available_for_appropriation"),
        (lambda it: re.search(r"\btotal revenue\b|revenue from operations|^revenue$|^sales$|^net sales$", name_of(it)), "revenue"),
        (lambda it: re.search(r"cost of (goods sold|sales|revenue)|^cogs$", name_of(it)), "cost_of_sales"),
        (lambda it: "gross profit" in name_of(it), "gross_profit"),
        (lambda it: re.search(r"operating profit|\bebit\b", name_of(it)), "operating_profit"),
        (lambda it: re.search(r"tax expense|income tax|provision for tax", name_of(it)), "tax"),
        (lambda it: re.search(r"^net profit$|profit for the year(?! before)|profit after tax", name_of(it)), "net_profit"),
    ]
    totals = apply_totals_aliases(line_items, total_rules)

    title_field = detect_statement_title(doc_text, ["PROFIT AND LOSS", "PROFIT & LOSS", "INCOME STATEMENT"])
    company_field = detect_company_name(doc_text)
    period_field = detect_reporting_period(doc_text)

    field_confidences = [f.get("confidence") for f in (title_field, company_field, period_field) if f]
    if line_items:
        field_confidences.append(STRICT_LABEL_MATCH)

    data: Dict[str, Any] = {
        "statement_title": title_field,
        "company_name": company_field,
        "reporting_period": period_field,
        "currency": make_field(currency, confidence=STRICT_LABEL_MATCH if currency else None, evidence=currency_evidence) if currency else make_field(None),
        "units": make_field(unit_label, confidence=STRICT_LABEL_MATCH if unit_label else None) if unit_label else make_field(None),
        "unit_multiplier": unit_multiplier,
        "periods": periods,
        "financial_line_items": line_items,
        "totals": totals,
    }
    data["_confidence_components"] = field_confidences
    return data


# ==========================================================================
# Cash flow statement
# ==========================================================================

def _classify_cash_flow_section(label: str) -> Optional[str]:
    upper = label.upper()
    if "OPERATING ACTIVIT" in upper:
        return "operating"
    if "INVESTING ACTIVIT" in upper:
        return "investing"
    if "FINANCING ACTIVIT" in upper:
        return "financing"
    return None


def extract_cash_flow_statement(doc_text: DocumentText) -> Dict[str, Any]:
    periods = detect_periods(doc_text)
    currency, currency_evidence, unit_multiplier, unit_label = detect_currency_and_units(doc_text)
    line_items = parse_financial_line_items(doc_text, periods, _classify_cash_flow_section)

    def name_of(it):
        return it["name"].lower()

    total_rules: List[Tuple[Callable[[Dict[str, Any]], bool], str]] = [
        (lambda it: it["section"] == "operating" and "net cash flow" in name_of(it), "operating_cash_flow"),
        (lambda it: it["section"] == "investing" and "net cash flow" in name_of(it), "investing_cash_flow"),
        (lambda it: it["section"] == "financing" and "net cash flow" in name_of(it), "financing_cash_flow"),
        (lambda it: re.search(r"fluctuation in foreign currency|exchange (rate )?(difference|adjustment)|translation reserve|effect of exchange", name_of(it)), "fx_translation_adjustment"),
        (lambda it: re.search(r"net increase in cash|net decrease in cash|net increase\s*/\s*\(decrease\)", name_of(it)), "net_change_in_cash"),
        (lambda it: re.search(r"cash and cash equivalents at the beginning|cash.*beginning of the (year|period)", name_of(it)), "opening_cash"),
        (lambda it: re.search(r"cash and cash equivalents at the end|cash.*end of the (year|period)", name_of(it)), "closing_cash"),
    ]
    totals = apply_totals_aliases(line_items, total_rules)

    title_field = detect_statement_title(doc_text, ["CASH FLOW"])
    company_field = detect_company_name(doc_text)
    period_field = detect_reporting_period(doc_text)

    field_confidences = [f.get("confidence") for f in (title_field, company_field, period_field) if f]
    if line_items:
        field_confidences.append(STRICT_LABEL_MATCH)

    data: Dict[str, Any] = {
        "statement_title": title_field,
        "company_name": company_field,
        "reporting_period": period_field,
        "currency": make_field(currency, confidence=STRICT_LABEL_MATCH if currency else None, evidence=currency_evidence) if currency else make_field(None),
        "units": make_field(unit_label, confidence=STRICT_LABEL_MATCH if unit_label else None) if unit_label else make_field(None),
        "unit_multiplier": unit_multiplier,
        "periods": periods,
        "financial_line_items": line_items,
        "totals": totals,
    }
    data["_confidence_components"] = field_confidences
    return data


# ==========================================================================
# Invoice
# ==========================================================================

_LABELED_PATTERNS: Dict[str, List[str]] = {
    "invoice_number": [
        r"(?:invoice|bill|receipt)\s*(?:no\.?|number|num|#)\s*[:\-]?\s*([A-Za-z0-9\/\-]+)",
        r"\bTRN\s*[:\-]?\s*([A-Za-z0-9\-]+)",
    ],
    "purchase_order_number": [r"(?:buyer'?s\s*)?(?:p\.?o\.?|purchase\s*order)\s*(?:no\.?|number)?\s*[:\-]?\s*([A-Za-z0-9\/\-]+)"],
    "tax_id": [r"\bGSTIN\s*/?\s*UIN\s*[:\-]?\s*([A-Za-z0-9]+)", r"\bGST\s*No\.?\s*[:\-]?\s*([A-Za-z0-9]+)", r"\bTAX\s*ID\s*[:\-]?\s*([A-Za-z0-9]+)"],
    "phone": [r"(?:contact|phone|tel|mobile)\s*(?:no\.?)?\s*[:\-]?\s*([\d][\d,\+\-\s]{6,18}\d)"],
    "email": [r"([\w.+-]+@[\w-]+\.[\w.-]+)"],
    "payment_terms": [r"(?:mode\s*/\s*)?terms?\s*of\s*payment\s*[:\-]?\s*([A-Za-z0-9 ,.\/]+)", r"payment\s*terms\s*[:\-]?\s*([A-Za-z0-9 ,.\/]+)"],
    "payment_method": [r"(?:mode\s*of\s*payment|payment\s*method)\s*[:\-]?\s*([A-Za-z0-9 ,.\/]+)"],
}

_AMOUNT_LABELS: Dict[str, List[str]] = {
    "subtotal": [r"sub\s*[\-]?\s*total", r"taxable\s+(?:value|amount)", r"net\s+amount"],
    "tax_amount": [r"total\s+tax\s+amount", r"total\s+gst", r"tax\s+amount", r"gst\s+amount", r"^gst\b.*%"],
    "discount": [r"discount"],
    "total_amount": [r"grand\s*total", r"total\s+amount\s+due", r"amount\s+payable", r"total\s+includes\s+gst", r"amount\s+chargeable", r"net\s+payable", r"^total\b(?!.*qty)"],
    "cash_paid": [r"\bcash\b"],
    "change_amount": [r"\bchange\b"],
}

_DATE_LABELS = [r"\binvoice\s*date\b", r"\bdated\b", r"\bdate\b"]
_DUE_DATE_LABELS = [r"due\s*date"]


def _find_labeled_value(lines: List[str], patterns: List[str]) -> Optional[Tuple[str, str]]:
    for line in lines:
        for pattern in patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match and match.groups():
                value = normalize_whitespace(match.group(1))
                if value:
                    return value, line
    return None


def _find_amount_field(lines: List[str], label_patterns: List[str]) -> Optional[Tuple[float, str]]:
    for line in lines:
        for pattern in label_patterns:
            if re.search(pattern, line, re.IGNORECASE):
                numbers = find_generic_numbers(line)
                if numbers:
                    return numbers[-1], line
    return None


def _extract_vendor_name(lines: List[str]) -> Optional[Dict[str, Any]]:
    skip_phrases = ("tax invoice", "invoice", "receipt", "cash bill", "original", "duplicate", "printed on")
    for line in lines[:6]:
        cleaned = normalize_whitespace(line)
        low = cleaned.lower()
        if not cleaned or any(low == p or low.startswith(p) for p in skip_phrases):
            continue
        if len(re.sub(r"[^A-Za-z]", "", cleaned)) < 3:
            continue
        return make_field(cleaned, confidence=LOOSE_LABEL_MATCH, page_number=1, evidence=line)
    return None


def _extract_party_block(lines: List[str], label_patterns: List[str], max_extra_lines: int = 3) -> Optional[Dict[str, Any]]:
    for idx, line in enumerate(lines):
        if any(re.search(p, line, re.IGNORECASE) for p in label_patterns):
            remainder = re.sub("|".join(label_patterns), "", line, flags=re.IGNORECASE).strip(" :-")
            block_lines = [remainder] if remainder else []
            block_lines += [normalize_whitespace(l) for l in lines[idx + 1: idx + 1 + max_extra_lines] if l.strip()]
            block_lines = [l for l in block_lines if l]
            if block_lines:
                value = ", ".join(block_lines[:max_extra_lines])
                return make_field(value, confidence=LOOSE_LABEL_MATCH, page_number=1, evidence=line)
    return None


def _extract_line_items(table_lines: List[str]) -> List[Dict[str, Any]]:
    header_idx = None
    header_keywords = ["qty", "quantity", "description", "item", "rate", "price", "amount", "particulars"]
    for idx, line in enumerate(table_lines):
        low = line.lower()
        hits = sum(1 for kw in header_keywords if kw in low)
        if hits >= 2:
            header_idx = idx
            break

    if header_idx is None:
        return []

    # Stop-boundary patterns anchor to the start of the row's actual text, but
    # OCR frequently prepends stray punctuation (`*Total Qty`, `"Total: ...`).
    # Strip leading non-alphanumeric noise before testing so the anchor still
    # lines up with the real first word.
    def _lstrip_noise(s: str) -> str:
        return re.sub(r"^[^A-Za-z0-9]+", "", s)

    end_idx = len(table_lines)
    stop_keywords = [r"^sub\s*[\-]?\s*total", r"^total\b", r"amount chargeable", r"gst summary", r"^tax\s+amount", r"declaration", r"bank details", r"authorised signatory", r"customer'?s payment"]
    for idx in range(header_idx + 1, len(table_lines)):
        if any(re.search(p, _lstrip_noise(table_lines[idx]), re.IGNORECASE) for p in stop_keywords):
            end_idx = idx
            break

    # Rows that are tax/summary breakdowns, not product lines (e.g. "GST6% + 0.51").
    skip_row_patterns = [r"^gst\s*\d+\s*%", r"^vat\s*\d+\s*%", r"^cgst\b", r"^sgst\b", r"^igst\b"]

    items: List[Dict[str, Any]] = []
    for line in table_lines[header_idx + 1: end_idx]:
        stripped = line.strip()
        if not stripped:
            continue
        if any(re.search(p, _lstrip_noise(stripped), re.IGNORECASE) for p in skip_row_patterns):
            continue
        matches = [m for m in GENERIC_NUMBER_RE.finditer(stripped) if parse_number(m.group(0)) is not None]
        if not matches:
            continue
        numbers = [parse_number(m.group(0)) for m in matches]

        # Two row shapes are both common: "<description> ... <price> <amount>"
        # (most invoices) and "<qty> <description> <price> <amount>" (receipts,
        # e.g. "1  KOTAK  8.49  8.49  9.00  SR"). Detect a leading quantity -
        # a short whole number at the very start of the row followed by more
        # numbers later - before falling back to the description-first shape.
        leading_qty = None
        desc_start = 0
        remaining_matches, remaining_numbers = matches, numbers
        first = matches[0]
        if first.start() <= 1 and numbers[0] is not None and float(numbers[0]).is_integer() and 0 <= numbers[0] < 1000 and len(matches) >= 2:
            leading_qty = numbers[0]
            desc_start = first.end()
            remaining_matches, remaining_numbers = matches[1:], numbers[1:]

        if not remaining_numbers:
            continue
        # A genuine amount column is a decimal money figure in this dataset's
        # convention; a bare integer here usually means the "numbers" are
        # actually part of a compound spec token (e.g. "48X230ML"), not table
        # columns, so such rows are rejected rather than fabricating a line item.
        if "." not in remaining_matches[-1].group(0):
            continue
        take_trailing = min(2, len(remaining_numbers))
        trailing_start = remaining_matches[-take_trailing].start()
        description = normalize_whitespace(stripped[desc_start:trailing_start])
        if not description or not re.search(r"[A-Za-z]{2,}", description):
            continue

        item: Dict[str, Any] = {"description": description}
        if leading_qty is not None:
            item["quantity"] = leading_qty
            if take_trailing == 2:
                item["unit_price"] = remaining_numbers[-2]
                item["amount"] = remaining_numbers[-1]
            else:
                item["amount"] = remaining_numbers[-1]
        elif len(remaining_numbers) >= 3:
            item["quantity"] = remaining_numbers[-3]
            item["unit_price"] = remaining_numbers[-2]
            item["amount"] = remaining_numbers[-1]
            if len(remaining_numbers) > 3:
                item["additional_amounts"] = remaining_numbers[:-3]
        elif len(remaining_numbers) == 2:
            item["unit_price"] = remaining_numbers[-2]
            item["amount"] = remaining_numbers[-1]
        else:
            item["amount"] = remaining_numbers[-1]
        item["evidence"] = stripped
        items.append(item)

    return items


def extract_invoice(doc_text: DocumentText) -> Dict[str, Any]:
    full_text = doc_text.full_text
    lines = []
    for page in doc_text.pages:
        lines.extend(split_lines(page.text))
    table_lines = []
    for page in doc_text.pages:
        table_lines.extend(page.table_lines or split_lines(page.text))

    data: Dict[str, Any] = {}
    field_confidences: List[Optional[float]] = []

    inv_no = _find_labeled_value(lines, _LABELED_PATTERNS["invoice_number"])
    data["invoice_number"] = make_field(inv_no[0], confidence=STRICT_LABEL_MATCH, evidence=inv_no[1], page_number=1) if inv_no else make_field(None)

    date_val = None
    for line in lines:
        if any(re.search(p, line, re.IGNORECASE) for p in _DATE_LABELS):
            found = find_date_in_text(line)
            if found:
                date_val = (normalize_date(found), line)
                break
    if not date_val:
        for line in lines[:15]:
            found = find_date_in_text(line)
            if found:
                date_val = (normalize_date(found), line)
                break
    data["invoice_date"] = make_field(date_val[0], confidence=STRICT_LABEL_MATCH if date_val else None, evidence=date_val[1] if date_val else None, page_number=1) if date_val else make_field(None)

    due_date = None
    for line in lines:
        if any(re.search(p, line, re.IGNORECASE) for p in _DUE_DATE_LABELS):
            found = find_date_in_text(line)
            if found:
                due_date = (normalize_date(found), line)
                break
    if due_date:
        data["due_date"] = make_field(due_date[0], confidence=STRICT_LABEL_MATCH, evidence=due_date[1], page_number=1)

    vendor = _extract_vendor_name(lines)
    data["vendor_name"] = vendor if vendor else make_field(None)

    customer = _extract_party_block(lines, [r"buyer\s*\(bill\s*to\)", r"\bbill\s*to\b", r"\bcustomer\b", r"\bsold\s*to\b"])
    data["customer_name"] = customer if customer else make_field(None)

    billing_address = _extract_party_block(lines, [r"\bbill\s*to\b", r"\bbuyer\b"], max_extra_lines=4)
    if billing_address:
        data["billing_address"] = billing_address
    shipping_address = _extract_party_block(lines, [r"\bship\s*to\b", r"\bconsignee\b"], max_extra_lines=4)
    if shipping_address:
        data["shipping_address"] = shipping_address

    currency = detect_currency(full_text)
    # "GST" alone is not India-specific (Malaysia, Singapore, Australia, Canada
    # all use the term) - only infer INR from identifiers unique to India's tax regime.
    if not currency and re.search(r"\bCGST\b|\bSGST\b|\bGSTIN\b", full_text, re.IGNORECASE):
        currency = "INR"
    data["currency"] = make_field(currency, confidence=STRICT_LABEL_MATCH if currency else None) if currency else make_field(None)

    for key in ("subtotal", "tax_amount", "discount", "total_amount", "cash_paid", "change_amount"):
        result = _find_amount_field(lines, _AMOUNT_LABELS[key])
        if result:
            data[key] = make_field(result[0], confidence=STRICT_LABEL_MATCH, evidence=result[1], page_number=1)
            field_confidences.append(STRICT_LABEL_MATCH)
        else:
            data[key] = make_field(None)

    for key, patterns in _LABELED_PATTERNS.items():
        if key in data:
            continue
        result = _find_labeled_value(lines, patterns)
        if result:
            data[key] = make_field(result[0], confidence=STRICT_LABEL_MATCH, evidence=result[1], page_number=1)
            field_confidences.append(STRICT_LABEL_MATCH)

    line_items = _extract_line_items(table_lines)
    data["line_items"] = line_items
    if line_items:
        field_confidences.append(LOOSE_LABEL_MATCH)

    for f in (data.get("invoice_number"), vendor, customer):
        if f and f.get("value") is not None:
            field_confidences.append(f.get("confidence", FALLBACK_MATCH))

    data["_confidence_components"] = field_confidences
    return data


# ==========================================================================
# Dispatch
# ==========================================================================

EXTRACTORS = {
    "invoice": extract_invoice,
    "balance_sheet": extract_balance_sheet,
    "profit_and_loss": extract_profit_and_loss,
    "cash_flow_statement": extract_cash_flow_statement,
}


def extract(document_type: str, doc_text: DocumentText) -> Dict[str, Any]:
    extractor = EXTRACTORS.get(document_type)
    if extractor is None:
        raise ValueError(f"No extractor registered for document type: {document_type}")
    return extractor(doc_text)
