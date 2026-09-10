"""Document processing API - controller layer only.

No OCR, extraction, validation or persistence logic lives here; every
handler delegates to `document_service`.
"""
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.exceptions import DocumentNotFoundError, InvalidDocumentTypeError, ProcessingError
from app.core.logging import get_logger
from app.schemas.document import DocumentListResponse, DocumentResponse, DocumentType
from app.services import document_service

router = APIRouter(prefix="/documents", tags=["documents"])
logger = get_logger(__name__)
settings = get_settings()

_VALID_DOCUMENT_TYPES = {t.value for t in DocumentType}


@router.post("/process", response_model=DocumentResponse)
async def process_document(
    file: UploadFile = File(..., description="PDF, JPG or PNG document to process"),
    document_type: str = Form(..., description="One of: invoice, balance_sheet, profit_and_loss, cash_flow_statement"),
    db: Session = Depends(get_db),
):
    if document_type not in _VALID_DOCUMENT_TYPES:
        raise InvalidDocumentTypeError(
            f"'{document_type}' is not a supported document_type. "
            f"Allowed values: {', '.join(sorted(_VALID_DOCUMENT_TYPES))}."
        )

    content = await file.read()

    try:
        result = document_service.process_document(db, content, file.filename or "upload", document_type)
    except Exception as exc:
        from app.core.exceptions import AppError

        if isinstance(exc, AppError):
            raise
        logger.exception("Unexpected processing failure | document=%s", file.filename)
        raise ProcessingError(f"Document processing failed: {exc}")

    return result


@router.get("", response_model=DocumentListResponse)
def list_documents(
    document_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    documents = document_service.list_documents(db, document_type=document_type, processing_status=status, search=search)
    return {"total": len(documents), "documents": documents}


@router.get("/{document_name}", response_model=DocumentResponse)
def get_document(document_name: str, db: Session = Depends(get_db)):
    result = document_service.get_document(db, document_name)
    if result is None:
        raise DocumentNotFoundError(f"No processed document found with name '{document_name}'.")
    return result
