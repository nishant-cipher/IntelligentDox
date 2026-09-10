"""Orchestrates the full document processing workflow.

validate -> save temp file -> OCR/text extraction -> structured extraction ->
financial validation -> confidence -> persist -> return response.

This is the only module that calls every other service - it contains no
OCR, extraction or validation logic of its own.
"""
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.repositories.document_repository import DocumentRepository
from app.services import extraction_service, financial_validation_service, llm_extraction_service, ocr_service
from app.services.document_validation_service import validate_upload
from app.utils.confidence_utils import ocr_quality_score, overall_confidence
from app.utils.file_utils import safe_filename

logger = get_logger(__name__)
settings = get_settings()


def _has_core_data(document_type: str, extracted: Dict[str, Any]) -> bool:
    if document_type == "invoice":
        invoice_number = (extracted.get("invoice_number") or {}).get("value")
        total_amount = (extracted.get("total_amount") or {}).get("value")
        return invoice_number is not None or total_amount is not None

    # Financial statements: require at least a handful of parsed line items
    # AND at least one populated total to consider extraction meaningful.
    line_items = extracted.get("financial_line_items") or []
    totals = extracted.get("totals") or {}
    has_totals = any(bool(v) for v in totals.values() if isinstance(v, dict))
    return len(line_items) >= 3 and has_totals


def determine_processing_status(document_type: str, extracted: Dict[str, Any], validation_result: Dict[str, Any]) -> str:
    """PASS requires meaningful extraction AND no failed (non-NOT_APPLICABLE)
    financial validation check. A NOT_APPLICABLE check never fails the
    document - only an actual arithmetic mismatch does."""
    if not _has_core_data(document_type, extracted):
        return "FAILED"
    if validation_result.get("overall_status") == "FAIL":
        return "FAILED"
    return "PASS"


def _compute_overall_confidence(doc_text: ocr_service.DocumentText, extracted_confidences) -> Optional[float]:
    ocr_scores = [ocr_quality_score(p.ocr_confidence) for p in doc_text.pages if p.ocr_confidence is not None]
    components = list(extracted_confidences) + ocr_scores
    return overall_confidence(components)


def process_document(db: Session, file_bytes: bytes, original_filename: str, document_type: str) -> Dict[str, Any]:
    start = time.perf_counter()
    logger.info("Processing started | document=%s | type=%s", original_filename, document_type)

    file_validation = validate_upload(original_filename, file_bytes)
    logger.info(
        "File validation passed | document=%s | file_type=%s | pages=%d",
        original_filename, file_validation.file_type, file_validation.page_count,
    )

    temp_path = os.path.join(settings.UPLOAD_DIR, safe_filename(original_filename))
    with open(temp_path, "wb") as fh:
        fh.write(file_bytes)

    try:
        logger.info("OCR/text extraction start | document=%s", original_filename)
        doc_text = ocr_service.extract_document_text(temp_path, file_validation.file_type)
        logger.info(
            "OCR/text extraction end | document=%s | ocr_used=%s | pages=%d",
            original_filename, doc_text.ocr_used, len(doc_text.pages),
        )

        logger.info("Structured extraction start | document=%s", original_filename)
        extracted = extraction_service.extract(document_type, doc_text)
        confidence_components = extracted.pop("_confidence_components", [])
        logger.info("Structured extraction end | document=%s", original_filename)

        extraction_provider = "rule_based"
        if llm_extraction_service.is_enabled():
            logger.info("LLM supplementary extraction start | document=%s", original_filename)
            llm_result = llm_extraction_service.extract_with_llm(document_type, doc_text.full_text)
            if llm_result:
                extracted = llm_extraction_service.merge_gaps(extracted, llm_result)
                extraction_provider = "rule_based+llm_gap_fill"
            logger.info("LLM supplementary extraction end | document=%s | used=%s", original_filename, bool(llm_result))

        logger.info("Financial validation start | document=%s", original_filename)
        validation_result = financial_validation_service.validate(document_type, extracted)
        logger.info(
            "Financial validation end | document=%s | status=%s | checks=%d",
            original_filename, validation_result["overall_status"], len(validation_result["checks"]),
        )
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass

    processing_status = determine_processing_status(document_type, extracted, validation_result)
    overall_conf = _compute_overall_confidence(doc_text, confidence_components)
    processing_time_ms = int((time.perf_counter() - start) * 1000)

    processing_metadata = {
        "ocr_used": doc_text.ocr_used,
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "processing_time_ms": processing_time_ms,
        "ocr_provider": "tesseract" if doc_text.ocr_used else None,
        "extraction_provider": extraction_provider,
        "page_count": file_validation.page_count,
        "model_used": None,
    }

    file_validation_dict = {
        "file_type": file_validation.file_type,
        "is_supported": file_validation.is_supported,
        "is_readable": file_validation.is_readable,
        "page_count": file_validation.page_count,
        "status": file_validation.status,
        "issues": file_validation.issues,
    }

    repo = DocumentRepository(db)
    record = repo.create(
        document_name=original_filename,
        document_type=document_type,
        processing_status=processing_status,
        file_type=file_validation.file_type,
        page_count=file_validation.page_count,
        is_supported=file_validation.is_supported,
        is_readable=file_validation.is_readable,
        overall_confidence=overall_conf,
        file_validation=file_validation_dict,
        extracted_data=extracted,
        validation_result=validation_result,
        processing_metadata=processing_metadata,
    )

    logger.info(
        "Processing complete | document=%s | status=%s | duration_ms=%d",
        original_filename, processing_status, processing_time_ms,
    )

    return _record_to_response(record)


def _record_to_response(record) -> Dict[str, Any]:
    return {
        "document_name": record.document_name,
        "document_type": record.document_type,
        "processing_status": record.processing_status,
        "overall_confidence": record.overall_confidence,
        "file_validation": record.file_validation,
        "extracted_data": record.extracted_data,
        "validation": record.validation_result,
        "processing_metadata": record.processing_metadata,
    }


def get_document(db: Session, document_name: str) -> Optional[Dict[str, Any]]:
    repo = DocumentRepository(db)
    record = repo.get_latest_by_name(document_name)
    if record is None:
        return None
    return _record_to_response(record)


def list_documents(db: Session, document_type: Optional[str] = None, processing_status: Optional[str] = None, search: Optional[str] = None):
    repo = DocumentRepository(db)
    records = repo.latest_per_document()
    if document_type:
        records = [r for r in records if r.document_type == document_type]
    if processing_status:
        records = [r for r in records if r.processing_status == processing_status]
    if search:
        needle = search.lower()
        records = [r for r in records if needle in r.document_name.lower()]
    return [
        {
            "document_name": r.document_name,
            "document_type": r.document_type,
            "processing_status": r.processing_status,
            "overall_confidence": r.overall_confidence,
            "page_count": r.page_count,
            "created_at": r.created_at.isoformat(),
            "updated_at": r.updated_at.isoformat(),
        }
        for r in records
    ]
