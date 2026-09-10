"""Top-level request/response schemas for the document processing API."""
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.schemas.extraction import FileValidation, ProcessingMetadata, ValidationResult


class DocumentType(str, Enum):
    INVOICE = "invoice"
    BALANCE_SHEET = "balance_sheet"
    PROFIT_AND_LOSS = "profit_and_loss"
    CASH_FLOW_STATEMENT = "cash_flow_statement"


class ProcessingStatus(str, Enum):
    PASS = "PASS"
    FAILED = "FAILED"


class DocumentResponse(BaseModel):
    document_name: str
    document_type: DocumentType
    processing_status: str
    overall_confidence: Optional[float] = None
    file_validation: FileValidation
    extracted_data: Dict[str, Any] = Field(default_factory=dict)
    validation: ValidationResult
    processing_metadata: ProcessingMetadata


class DocumentListItem(BaseModel):
    document_name: str
    document_type: DocumentType
    processing_status: str
    overall_confidence: Optional[float] = None
    page_count: Optional[int] = None
    created_at: str
    updated_at: str


class DocumentListResponse(BaseModel):
    total: int
    documents: List[DocumentListItem]


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class HealthResponse(BaseModel):
    status: str
    database: Optional[str] = None
    ocr_engine: Optional[str] = None
    version: Optional[str] = None
