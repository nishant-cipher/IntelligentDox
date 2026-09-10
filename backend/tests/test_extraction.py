"""Tests for extraction_service and number_utils, run against real dataset
documents (not fabricated data) so they exercise the actual OCR + parsing
pipeline end to end."""
import pytest

from app.services import extraction_service, financial_validation_service, ocr_service
from app.utils.number_utils import detect_currency, detect_unit_multiplier, parse_number

from .conftest import dataset_file, skip_if_no_dataset


class TestNumberParsing:
    def test_plain_number(self):
        assert parse_number("1250.50") == 1250.50

    def test_thousands_separator(self):
        assert parse_number("1,250.50") == 1250.50

    def test_parentheses_are_negative(self):
        assert parse_number("(1,250.50)") == -1250.50

    def test_bracket_negative(self):
        assert parse_number("[500]") == -500.0

    def test_currency_prefix(self):
        assert parse_number("USD 13,125.00") == 13125.00
        assert parse_number("₹ 25,000") == 25000.0

    def test_lone_dash_is_none_by_default(self):
        assert parse_number("-") is None

    def test_lone_dash_as_zero_when_requested(self):
        assert parse_number("-", treat_lone_dash_as_zero=True) == 0.0

    def test_garbage_returns_none(self):
        assert parse_number("N/A") is None
        assert parse_number("") is None
        assert parse_number(None) is None


class TestUnitAndCurrencyDetection:
    def test_crore_detected(self):
        mult, label = detect_unit_multiplier("(₹ in crore)")
        assert mult == 10_000_000
        assert label == "crore"

    def test_no_unit_when_not_stated(self):
        mult, label = detect_unit_multiplier("Total amount due")
        assert mult is None
        assert label is None

    def test_currency_symbol(self):
        assert detect_currency("Total: ₹1,000") == "INR"
        assert detect_currency("Total: $1,000") == "USD"

    def test_no_currency_when_absent(self):
        assert detect_currency("Total: 1,000") is None


@skip_if_no_dataset
class TestBalanceSheetExtraction:
    @pytest.fixture(scope="class")
    def extracted(self):
        path = dataset_file("Balance Sheet", "Consolidated Balance Sheet 2026.pdf")
        doc_text = ocr_service.extract_text_from_pdf(str(path))
        return extraction_service.extract_balance_sheet(doc_text)

    def test_periods_detected(self, extracted):
        assert extracted["periods"] == ["2026", "2025"]

    def test_totals_present_and_nonzero(self, extracted):
        totals = extracted["totals"]
        assert totals["total_assets"]["2026"] > 0
        assert totals["total_liabilities"]["2026"] > 0

    def test_line_items_extracted(self, extracted):
        names = [li["name"] for li in extracted["financial_line_items"]]
        assert any("Capital" in n for n in names)
        assert any("Deposits" in n for n in names)

    def test_company_name(self, extracted):
        assert extracted["company_name"]["value"] == "HDFC Bank Limited"

    def test_missing_field_is_null_not_fabricated(self, extracted):
        # total_equity is not explicitly labeled in this bank-format statement,
        # and must not be invented - it should come back empty, not guessed.
        assert extracted["totals"]["total_equity"] == {}


@skip_if_no_dataset
class TestProfitAndLossExtraction:
    @pytest.fixture(scope="class")
    def extracted(self):
        path = dataset_file("Profit & Loss", "Consolidated Profit & Loss 2026.pdf")
        doc_text = ocr_service.extract_text_from_pdf(str(path))
        return extraction_service.extract_profit_and_loss(doc_text)

    def test_totals_present(self, extracted):
        totals = extracted["totals"]
        assert totals["total_income"]["2026"] > 0
        assert totals["total_expenditure"]["2026"] > 0

    def test_two_periods(self, extracted):
        assert len(extracted["periods"]) == 2


@skip_if_no_dataset
class TestCashFlowExtraction:
    @pytest.fixture(scope="class")
    def extracted(self):
        path = dataset_file("Cash Flows", "Consolidated Cash Flow Statement 2026.pdf")
        doc_text = ocr_service.extract_text_from_pdf(str(path))
        return extraction_service.extract_cash_flow_statement(doc_text)

    def test_closing_cash_present(self, extracted):
        assert extracted["totals"]["closing_cash"]["2026"] > 0

    def test_opening_plus_change_relation(self, extracted):
        totals = extracted["totals"]
        opening = totals["opening_cash"]["2026"]
        closing = totals["closing_cash"]["2026"]
        net_change = totals["net_change_in_cash"]["2026"]
        assert abs((opening + net_change) - closing) < 1.0


@skip_if_no_dataset
class TestInvoiceExtraction:
    def test_receipt_invoice_core_fields(self):
        path = dataset_file("Invoices", "X51005361895.jpg")
        doc_text = ocr_service.extract_text_from_image(str(path))
        extracted = extraction_service.extract_invoice(doc_text)
        assert extracted["total_amount"]["value"] == pytest.approx(9.0)
        assert extracted["cash_paid"]["value"] == pytest.approx(50.0)
        assert extracted["change_amount"]["value"] == pytest.approx(41.0)

    def test_negative_numbers_never_invented_when_absent(self):
        # A minimal, clearly-empty document should not produce fabricated values.
        empty_doc = ocr_service.DocumentText(
            pages=[ocr_service.PageText(page_number=1, text="", row_text="")], ocr_used=False
        )
        extracted = extraction_service.extract_invoice(empty_doc)
        assert extracted["invoice_number"]["value"] is None
        assert extracted["total_amount"]["value"] is None
        assert extracted["line_items"] == []


class TestFinancialValidationService:
    """Deterministic checks against financial_validation_service directly,
    covering all three possible outcomes: PASS, FAIL, NOT_APPLICABLE."""

    def test_invoice_total_check_passes_when_numbers_reconcile(self):
        extracted = {
            "subtotal": {"value": 100.0},
            "tax_amount": {"value": 10.0},
            "discount": {"value": 5.0},
            "total_amount": {"value": 105.0},
            "line_items": [],
        }
        result = financial_validation_service.validate_invoice(extracted)
        check = next(c for c in result["checks"] if c["name"] == "invoice_total_check")
        assert check["status"] == "PASS"
        assert check["variance"] == 0.0

    def test_invoice_total_check_fails_when_numbers_do_not_reconcile(self):
        extracted = {
            "subtotal": {"value": 100.0},
            "tax_amount": {"value": 10.0},
            "discount": {"value": 5.0},
            "total_amount": {"value": 110.0},  # should be 105
            "line_items": [],
        }
        result = financial_validation_service.validate_invoice(extracted)
        check = next(c for c in result["checks"] if c["name"] == "invoice_total_check")
        assert check["status"] == "FAIL"
        assert check["variance"] == pytest.approx(-5.0)
        assert result["overall_status"] == "FAIL"
        assert len(result["issues"]) >= 1

    def test_invoice_total_check_not_applicable_when_fields_missing(self):
        extracted = {
            "subtotal": {"value": None},
            "tax_amount": {"value": None},
            "discount": {"value": None},
            "total_amount": {"value": None},
            "line_items": [],
        }
        result = financial_validation_service.validate_invoice(extracted)
        check = next(c for c in result["checks"] if c["name"] == "invoice_total_check")
        assert check["status"] == "NOT_APPLICABLE"
        assert result["overall_status"] == "NOT_APPLICABLE"

    def test_balance_sheet_assets_equal_liabilities_pass(self):
        extracted = {
            "periods": ["2024"],
            "financial_line_items": [],
            "totals": {
                "total_assets": {"2024": 1000.0},
                "total_liabilities": {"2024": 1000.0},
            },
        }
        result = financial_validation_service.validate_balance_sheet(extracted)
        assert result["overall_status"] == "PASS"

    def test_balance_sheet_mismatch_fails(self):
        extracted = {
            "periods": ["2024"],
            "financial_line_items": [],
            "totals": {
                "total_assets": {"2024": 1000.0},
                "total_liabilities": {"2024": 950.0},
            },
        }
        result = financial_validation_service.validate_balance_sheet(extracted)
        assert result["overall_status"] == "FAIL"

    def test_cash_flow_not_applicable_without_data(self):
        result = financial_validation_service.validate("cash_flow_statement", {"periods": [], "financial_line_items": [], "totals": {}})
        assert result["overall_status"] == "NOT_APPLICABLE"
        assert result["checks"] == []
