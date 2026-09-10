"""Financial calculation validation engine.

Pure arithmetic checks over already-extracted structured data. This module
never touches OCR or raw text - it only reads numbers that extraction_service
has already parsed, recomputes the case-study formulas, and compares the
recomputed value against what the document itself reports.

Every check returns PASS / FAIL / NOT_APPLICABLE (never fabricated) and
carries the operands + calculated/reported values so the result is fully
auditable from the API response alone.
"""
from typing import Any, Dict, List, Optional

from app.core.config import get_settings
from app.utils.number_utils import numbers_match

settings = get_settings()


def build_check(
    name: str,
    formula: str,
    operands: Dict[str, Optional[float]],
    calculated_value: Optional[float],
    reported_value: Optional[float],
    period: Optional[str] = None,
    message: Optional[str] = None,
) -> Dict[str, Any]:
    if calculated_value is None or reported_value is None:
        status = "NOT_APPLICABLE"
        variance = None
        if message is None:
            message = "Required fields for this check were not found in the document."
    else:
        variance = round(calculated_value - reported_value, 4)
        status = "PASS" if numbers_match(
            calculated_value, reported_value,
            settings.VALIDATION_ABSOLUTE_TOLERANCE, settings.VALIDATION_RELATIVE_TOLERANCE,
        ) else "FAIL"
        if status == "FAIL" and message is None:
            message = f"Calculated value differs from reported value by {variance}."

    return {
        "name": name,
        "formula": formula,
        "operands": operands,
        "calculated_value": calculated_value,
        "reported_value": reported_value,
        "variance": variance,
        "status": status,
        "period": period,
        "message": message,
    }


def _sum_if_all_present(values: List[Optional[float]]) -> Optional[float]:
    if not values or any(v is None for v in values):
        return None
    return round(sum(values), 4)


def _finalize(checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    issues = [
        f"{c['name']}" + (f" ({c['period']})" if c.get("period") else "") + f": {c['message']}"
        for c in checks
        if c["status"] == "FAIL"
    ]
    applicable = [c for c in checks if c["status"] != "NOT_APPLICABLE"]
    if any(c["status"] == "FAIL" for c in checks):
        overall = "FAIL"
    elif applicable:
        overall = "PASS"
    else:
        overall = "NOT_APPLICABLE"
    return {"checks": checks, "overall_status": overall, "issues": issues}


# --------------------------------------------------------------------------
# Invoice
# --------------------------------------------------------------------------

def validate_invoice(extracted: Dict[str, Any]) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    def field_value(key: str) -> Optional[float]:
        field = extracted.get(key)
        if isinstance(field, dict):
            return field.get("value")
        return None

    subtotal = field_value("subtotal")
    tax_amount = field_value("tax_amount")
    discount = field_value("discount")
    total_amount = field_value("total_amount")
    cash_paid = field_value("cash_paid")
    change_amount = field_value("change_amount")

    line_items = extracted.get("line_items") or []
    for idx, item in enumerate(line_items, start=1):
        qty = item.get("quantity")
        unit_price = item.get("unit_price")
        amount = item.get("amount")
        calculated = round(qty * unit_price, 4) if qty is not None and unit_price is not None else None
        checks.append(
            build_check(
                name=f"line_item_{idx}_amount_check",
                formula="quantity * unit_price",
                operands={"quantity": qty, "unit_price": unit_price},
                calculated_value=calculated,
                reported_value=amount,
            )
        )

    if line_items:
        amounts = [li.get("amount") for li in line_items]
        line_items_sum = _sum_if_all_present(amounts)
        checks.append(
            build_check(
                name="line_items_sum_matches_subtotal",
                formula="sum(line_items.amount)",
                operands={f"item_{i+1}": a for i, a in enumerate(amounts)},
                calculated_value=line_items_sum,
                reported_value=subtotal if subtotal is not None else total_amount,
                message=None,
            )
        )

    # Always emit these checks (even when NOT_APPLICABLE) so a caller can see
    # every formula the engine knows about and why it could or couldn't run.
    calculated_total = None
    if subtotal is not None and total_amount is not None:
        calculated_total = round(subtotal + (tax_amount or 0.0) - (discount or 0.0), 4)
    checks.append(
        build_check(
            name="invoice_total_check",
            formula="subtotal + tax_amount - discount",
            operands={"subtotal": subtotal, "tax_amount": tax_amount, "discount": discount},
            calculated_value=calculated_total,
            reported_value=total_amount,
        )
    )

    calculated_change = None
    if cash_paid is not None and total_amount is not None:
        calculated_change = round(cash_paid - total_amount, 4)
    checks.append(
        build_check(
            name="cash_change_check",
            formula="cash_paid - total_amount",
            operands={"cash_paid": cash_paid, "total_amount": total_amount},
            calculated_value=calculated_change,
            reported_value=change_amount,
        )
    )

    return _finalize(checks)


# --------------------------------------------------------------------------
# Balance sheet
# --------------------------------------------------------------------------

def validate_balance_sheet(extracted: Dict[str, Any]) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    periods: List[str] = extracted.get("periods") or []
    totals = extracted.get("totals") or {}
    line_items = extracted.get("financial_line_items") or []

    total_assets = totals.get("total_assets") or {}
    total_liabilities = totals.get("total_liabilities") or {}

    for period in periods:
        ta = total_assets.get(period)
        tl = total_liabilities.get(period)
        checks.append(
            build_check(
                name="total_assets_equals_total_liabilities_and_equity",
                formula="Total Capital & Liabilities == Total Assets",
                operands={"total_liabilities_and_equity": tl, "total_assets": ta},
                calculated_value=tl,
                reported_value=ta,
                period=period,
            )
        )

        for section, calc_name, reported in (
            ("assets", "sum_of_asset_line_items_equals_total_assets", ta),
            ("liabilities_and_equity", "sum_of_liability_line_items_equals_total", tl),
        ):
            # Exclude "Total"/subtotal rows themselves - only sum the actual
            # components, otherwise the total would be double-counted.
            section_items = [li for li in line_items if li.get("section") == section and not li.get("is_total")]
            values = [li.get("values", {}).get(period) for li in section_items]
            values = [v for v in values if v is not None]
            if len(values) >= 2 and len(values) == len(section_items) and section_items:
                calculated = round(sum(values), 4)
                checks.append(
                    build_check(
                        name=calc_name,
                        formula=f"sum(line_items[{section}].values[{period}])",
                        operands={li["name"]: li.get("values", {}).get(period) for li in section_items},
                        calculated_value=calculated,
                        reported_value=reported,
                        period=period,
                    )
                )

    return _finalize(checks)


# --------------------------------------------------------------------------
# Profit & Loss
# --------------------------------------------------------------------------

def validate_profit_and_loss(extracted: Dict[str, Any]) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    periods: List[str] = extracted.get("periods") or []
    totals = extracted.get("totals") or {}

    def t(key: str, period: str) -> Optional[float]:
        return (totals.get(key) or {}).get(period)

    for period in periods:
        interest_earned = t("interest_earned", period)
        other_income = t("other_income", period)
        total_income = t("total_income", period)
        checks.append(
            build_check(
                "total_income_check",
                "Interest Earned + Other Income == Total Income",
                {"interest_earned": interest_earned, "other_income": other_income},
                _sum_if_all_present([interest_earned, other_income]),
                total_income,
                period=period,
            )
        )

        interest_expended = t("interest_expended", period)
        operating_expenses = t("operating_expenses", period)
        provisions = t("provisions_and_contingencies", period)
        total_expenditure = t("total_expenditure", period)
        checks.append(
            build_check(
                "total_expenditure_check",
                "Interest Expended + Operating Expenses + Provisions & Contingencies == Total Expenditure",
                {
                    "interest_expended": interest_expended,
                    "operating_expenses": operating_expenses,
                    "provisions_and_contingencies": provisions,
                },
                _sum_if_all_present([interest_expended, operating_expenses, provisions]),
                total_expenditure,
                period=period,
            )
        )

        net_profit_before_mi = t("net_profit_before_minority_interest", period)
        calculated_net_profit = None
        if total_income is not None and total_expenditure is not None:
            calculated_net_profit = round(total_income - total_expenditure, 4)
        checks.append(
            build_check(
                "net_profit_before_minority_interest_check",
                "Total Income - Total Expenditure == Consolidated Net Profit before Minority Interest",
                {"total_income": total_income, "total_expenditure": total_expenditure},
                calculated_net_profit,
                net_profit_before_mi,
                period=period,
            )
        )

        minority_interest = t("minority_interest", period)
        net_profit_attributable = t("net_profit_attributable_to_group", period)
        calculated_attributable = None
        if net_profit_before_mi is not None and minority_interest is not None:
            calculated_attributable = round(net_profit_before_mi - minority_interest, 4)
        checks.append(
            build_check(
                "net_profit_attributable_to_group_check",
                "Profit before Minority Interest - Minority Interest == Net Profit attributable to the Group",
                {"net_profit_before_minority_interest": net_profit_before_mi, "minority_interest": minority_interest},
                calculated_attributable,
                net_profit_attributable,
                period=period,
            )
        )

        brought_forward = t("brought_forward_profit", period)
        total_available = t("total_available_for_appropriation", period)
        calculated_available = None
        if net_profit_attributable is not None and brought_forward is not None:
            calculated_available = round(net_profit_attributable + brought_forward, 4)
        checks.append(
            build_check(
                "total_available_for_appropriation_check",
                "Current Profit + Brought Forward Profit == Total Available for Appropriation",
                {"net_profit_attributable_to_group": net_profit_attributable, "brought_forward_profit": brought_forward},
                calculated_available,
                total_available,
                period=period,
            )
        )

        revenue = t("revenue", period)
        cost_of_sales = t("cost_of_sales", period)
        gross_profit = t("gross_profit", period)
        if revenue is not None and cost_of_sales is not None:
            checks.append(
                build_check(
                    "gross_profit_check",
                    "Revenue - Cost of Sales == Gross Profit",
                    {"revenue": revenue, "cost_of_sales": cost_of_sales},
                    round(revenue - cost_of_sales, 4),
                    gross_profit,
                    period=period,
                )
            )

    return _finalize(checks)


# --------------------------------------------------------------------------
# Cash flow statement
# --------------------------------------------------------------------------

def validate_cash_flow(extracted: Dict[str, Any]) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    periods: List[str] = extracted.get("periods") or []
    totals = extracted.get("totals") or {}

    def t(key: str, period: str) -> Optional[float]:
        return (totals.get(key) or {}).get(period)

    for period in periods:
        operating = t("operating_cash_flow", period)
        investing = t("investing_cash_flow", period)
        financing = t("financing_cash_flow", period)
        fx = t("fx_translation_adjustment", period)
        net_increase = t("net_change_in_cash", period)

        components = [operating, investing, financing]
        calculated_net_increase = None
        operands = {"operating": operating, "investing": investing, "financing": financing, "fx_adjustment": fx}
        if all(c is not None for c in components):
            calculated_net_increase = round(sum(components) + (fx or 0.0), 4)
        checks.append(
            build_check(
                "net_increase_in_cash_check",
                "Operating + Investing + Financing + FX Adjustment == Net Increase in Cash",
                operands,
                calculated_net_increase,
                net_increase,
                period=period,
            )
        )

        opening_cash = t("opening_cash", period)
        closing_cash = t("closing_cash", period)
        calculated_closing = None
        if opening_cash is not None and net_increase is not None:
            calculated_closing = round(opening_cash + net_increase, 4)
        checks.append(
            build_check(
                "closing_cash_check",
                "Opening Cash + Net Increase in Cash == Closing Cash",
                {"opening_cash": opening_cash, "net_increase_in_cash": net_increase},
                calculated_closing,
                closing_cash,
                period=period,
            )
        )

    return _finalize(checks)


VALIDATORS = {
    "invoice": validate_invoice,
    "balance_sheet": validate_balance_sheet,
    "profit_and_loss": validate_profit_and_loss,
    "cash_flow_statement": validate_cash_flow,
}


def validate(document_type: str, extracted: Dict[str, Any]) -> Dict[str, Any]:
    validator = VALIDATORS.get(document_type)
    if validator is None:
        return {"checks": [], "overall_status": "NOT_APPLICABLE", "issues": []}
    return validator(extracted)
