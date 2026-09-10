"""Pydantic models describing extraction, evidence and validation structures.

`extracted_data` in the final API response is a flexible dict (documents differ
too much field-to-field to force one rigid schema), but every leaf value placed
into it by the extraction services is built through these shapes so the JSON
stays consistent and explainable.
"""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

ValidationStatus = Literal["PASS", "FAIL", "NOT_APPLICABLE"]


class Evidence(BaseModel):
    """Grounding for an extracted value: the literal source text and its page."""

    source_text: Optional[str] = None
    page_number: Optional[int] = None


class ExtractedField(BaseModel):
    """A single extracted value with optional confidence and grounding."""

    value: Optional[Any] = None
    confidence: Optional[float] = None
    page_number: Optional[int] = None
    evidence: Optional[str] = None


class LineItem(BaseModel):
    """A row of an invoice line-item table.

    Extra columns actually present on the source document (tax, discount,
    hsn, sku, item_code, gst, ...) are preserved as additional keys because
    `extra="allow"`.
    """

    model_config = ConfigDict(extra="allow")

    description: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    amount: Optional[float] = None


class FinancialLineItem(BaseModel):
    """A single row of a financial statement (balance sheet / P&L / cash flow)."""

    section: Optional[str] = None
    name: str
    values: Dict[str, Optional[float]] = Field(default_factory=dict)
    page_number: Optional[int] = None
    evidence: Optional[str] = None


class ValidationCheck(BaseModel):
    name: str
    formula: str
    operands: Dict[str, Optional[float]] = Field(default_factory=dict)
    calculated_value: Optional[float] = None
    reported_value: Optional[float] = None
    variance: Optional[float] = None
    status: ValidationStatus
    period: Optional[str] = None
    message: Optional[str] = None


class ValidationResult(BaseModel):
    checks: List[ValidationCheck] = Field(default_factory=list)
    overall_status: ValidationStatus = "NOT_APPLICABLE"
    issues: List[str] = Field(default_factory=list)


class FileValidation(BaseModel):
    file_type: Optional[str] = None
    is_supported: bool
    is_readable: bool
    page_count: Optional[int] = None
    status: Literal["PASS", "FAIL"]
    issues: List[str] = Field(default_factory=list)


class ProcessingMetadata(BaseModel):
    ocr_used: bool = False
    processed_at: str
    processing_time_ms: int
    ocr_provider: Optional[str] = None
    extraction_provider: Optional[str] = "rule_based"
    page_count: Optional[int] = None
    model_used: Optional[str] = None
